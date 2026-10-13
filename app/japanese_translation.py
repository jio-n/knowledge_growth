"""Finite batches, strict IDs and safe reusable TranslationUnits (existing table)."""
from collections import Counter, deque
from contextlib import closing
import json
import re
import time
from typing import Literal
from pydantic import Field, ValidationError
from .ai.compat import RuntimeProvider, RuntimeLLMError
from .ai.base import AIError
from .config import PROMPTS_DIR
from .db import get_db, new_id, now
from .import_bridge.validator import canonical, _object, _constant
from .llm.base import LLMError
from .paper_brief import StrictModel

PROMPT_VERSION = 'japanese-pdf-0.3'
MAX_BATCH_CHARS = 6000
MAX_BLOCK_CHARS = 5000
MAX_BATCH_BLOCKS = 8
MAX_UNITS = 160
MAX_BATCHES = 24
MAX_ATTEMPTS = 2  # per batch size; then split unresolved blocks
MAX_RUNTIME_CALLS = 48  # shared by all batches, retries and splits
MAX_UNIT_ATTEMPTS = 8
MAX_TRANSLATION_SECONDS = 600
MAX_TURN_SECONDS = 120


class PDFError(ValueError):
    """Only fixed error codes may cross the API/log boundary."""


class TranslatedBlock(StrictModel):
    block_id: str = Field(min_length=1, max_length=64)
    translated_text: str = Field(max_length=12000)
    status: Literal['translated', 'uncertain']


class TranslationOutput(StrictModel):
    blocks: list[TranslatedBlock] = Field(max_length=MAX_BATCH_BLOCKS)


# Only these application-owned codes enter TranslationUnit.reason/report/UI.
TRANSLATION_ERRORS = frozenset({
    'ai_runtime_error', 'ai_rpc_error', 'ai_timeout', 'ai_incomplete_turn', 'ai_cancelled',
    'invalid_json', 'schema_mismatch', 'block_id_mismatch', 'numbers_changed',
    'citations_changed', 'references_changed', 'terms_changed', 'japanese_required',
    'ai_uncertain', 'translation_validation_error', 'output_too_large',
    'translation_attempt_limit', 'translation_time_limit',
    'assembled_user_edit_review',
})


class TranslationFailure(ValueError):
    def __init__(self, code):
        self.code = code if code in TRANSLATION_ERRORS else 'translation_validation_error'
        super().__init__(self.code)


def runtime_failure(error):
    # No str(error), vendor text, raw output or credential ever crosses this boundary.
    code = error.code if isinstance(error, (RuntimeLLMError, AIError)) else None
    if isinstance(error, TimeoutError) or code in ('rpc_timeout', 'turn_timeout'):
        return 'ai_timeout'
    if code == 'incomplete_turn': return 'ai_incomplete_turn'
    if code == 'cancelled': return 'ai_cancelled'
    if code == 'output_too_large': return 'output_too_large'
    if code in ('malformed_protocol', 'malformed_turn', 'malformed_turn_event',
                'process_disconnected', 'process_exited', 'rpc_rejected',
                'write_queue_full', 'request_too_large'):
        return 'ai_rpc_error'
    return 'ai_runtime_error'


def numerical_tokens(text):
    # Precision, signs, version numbers and scientific notation remain exact.
    # Thousands separators and percentages are preserved, not rounded/normalized.
    # Citations have their own ordered validation. Do not mistake [1,234] for
    # a thousands-separated value or reject a harmless [1, 234] spacing change.
    text = re.sub(r'\[\s*[0-9]+(?:\s*[,;–—-]\s*[0-9]+)*\s*\]', '', text)
    number = r'[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)*)(?:[eE][+-]?[0-9]+)?(?:\s*%)?'
    return Counter(re.sub(r'\s+', '', token) for token in re.findall(number, text))


def citation_tokens(text):
    # Ordinary bracketed numeric references; whitespace may differ, order may not.
    return Counter(re.sub(r'\s+', '', token) for token in
                   re.findall(r'\[\s*[0-9]+(?:\s*[,;–—-]\s*[0-9]+)*\s*\]', text))


def reference_tokens(text):
    patterns = {'figure': r'(?:Figures?|Figs?\.|図)', 'table': r'(?:Tables?|表)',
                'equation': r'(?:Equations?|Eqs?\.|式)'}
    return Counter((kind, number) for kind, pattern in patterns.items()
                   for number in re.findall(pattern+r'\s*\(?([0-9]+)\)?', text, re.I))


def protected_terms(text):
    # Conservative identifiers, not an attempt to infer every proper noun.
    # Also protects digit-bearing model names (GPT4o, ViT-B/16, Llama-3.1).
    candidates = re.findall(r'(?<![A-Za-z0-9_])(?:[A-Za-z][A-Za-z0-9]*(?:[-/][A-Za-z0-9.]+)+|[A-Za-z]+[0-9][A-Za-z0-9.]*)(?![A-Za-z0-9_])', text)
    terms = {t.rstrip('.') for t in candidates if any(c.isdigit() for c in t) or re.search(r'[A-Z]{2}|[a-z][A-Z]', t)}
    terms.update(re.findall(r'(?<![A-Za-z0-9_])(?:MMAD|CLIP|LoRA|ViT|GPT|AUROC)(?![A-Za-z0-9_])', text))
    return terms


def validate_block(item, original):
    if item.status == 'uncertain':
        if item.translated_text: raise TranslationFailure('schema_mismatch')
        raise TranslationFailure('ai_uncertain')
    translated = item.translated_text
    if not re.search(r'[\u3040-\u30ff\u3400-\u9fff]', translated):
        raise TranslationFailure('japanese_required')
    if numerical_tokens(original) != numerical_tokens(translated):
        raise TranslationFailure('numbers_changed')
    if citation_tokens(original) != citation_tokens(translated):
        raise TranslationFailure('citations_changed')
    if reference_tokens(original) != reference_tokens(translated):
        raise TranslationFailure('references_changed')
    if not protected_terms(original).issubset(protected_terms(translated)):
        raise TranslationFailure('terms_changed')


def parse_reply(text):
    if not isinstance(text, str): raise TranslationFailure('invalid_json')
    if len(text) > 128000: raise TranslationFailure('output_too_large')
    text = text.strip()
    # Accept only one complete JSON fence, not prose, substring extraction or repair.
    if text.startswith('```'):
        match = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```', text, re.S | re.I)
        if not match: raise TranslationFailure('invalid_json')
        text = match[1].strip()
    try:
        value = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, TypeError, RecursionError):
        raise TranslationFailure('invalid_json') from None
    if (not isinstance(value, dict) or set(value) != {'blocks'} or
            not isinstance(value['blocks'], list) or len(value['blocks']) > MAX_BATCH_BLOCKS):
        raise TranslationFailure('schema_mismatch')
    return value['blocks']


def validate_batch(text, batch):
    """Malformed envelope fails the batch; individually invalid rows cannot taint peers."""
    rows = parse_reply(text)
    expected = {b['id']: b['text'] for b in batch}
    ids = Counter(row.get('block_id') for row in rows if isinstance(row, dict) and isinstance(row.get('block_id'), str))
    errors, valid = {}, {}
    for bid, original in expected.items():
        if ids[bid] != 1:
            errors[bid] = 'block_id_mismatch'; continue
        row = next(r for r in rows if isinstance(r, dict) and r.get('block_id') == bid)
        try:
            item = TranslatedBlock.model_validate(row)
            validate_block(item, original)
            valid[bid] = item
        except ValidationError:
            errors[bid] = 'schema_mismatch'
        except TranslationFailure as error:
            errors[bid] = error.code
        except (ValueError, TypeError, RecursionError):
            errors[bid] = 'translation_validation_error'
    # Unknown IDs / unidentifiable rows are rejected, never persisted. Valid expected
    # rows can still be used; record the envelope anomaly independently.
    id_anomaly = any(not isinstance(r, dict) or not isinstance(r.get('block_id'), str)
                     or r['block_id'] not in expected for r in rows)
    return valid, errors, 'block_id_mismatch' if id_anomaly else None


def validate_reply(text, batch):
    """Strict compatibility entrypoint; generation uses independent validate_batch."""
    valid, errors, anomaly = validate_batch(text, batch)
    if errors or anomaly: raise TranslationFailure(anomaly or next(iter(errors.values())))
    return TranslationOutput(blocks=list(valid.values()))


def translation_diagnostics(units, stats, *, published=False, failure_stage=None):
    candidates = [u for u in units if u.get('translation_candidate')]
    failures = Counter(u['translation_error'] for u in candidates if u.get('translation_error'))
    return {'translation_candidates':len(candidates),
            'translation_succeeded':sum(bool(u.get('translation_succeeded')) for u in candidates),
            'ai_translation_succeeded':sum(bool(u.get('translation_succeeded')) and not u.get('cached') for u in candidates),
            'cached_translations':sum(bool(u.get('cached')) for u in candidates),
            'pdf_placement_succeeded':sum(u['status']=='translated' for u in units) if published else 0,
            'protected_units':sum(u['status']=='skipped_protected' and u['kind']=='visual' for u in units),
            'metadata_units':sum(u['kind']=='metadata' for u in units),
            'unsafe_units':sum(u['status']=='skipped_unsafe' for u in units),
            'translation_failures':dict(failures), 'failure_stage':failure_stage,
            **stats}


def cached_translation(con, source, version, block):
    # Legacy rows without source_hash are reusable only on exact version/text match.
    return con.execute('''SELECT * FROM translations WHERE source_id=? AND version_id=?
        AND block_id=? AND source_text=? AND translated_text<>''
        AND (source_hash=? OR source_hash IS NULL)
        ORDER BY user_edited DESC,updated_at DESC,rowid DESC LIMIT 1''',
        (source['id'], version['id'], block['id'], block['text'], version['content_hash'])).fetchone()


def save_unit(con, source, version, unit):
    b = unit['block']
    row = con.execute('''SELECT * FROM translations WHERE source_id=? AND version_id=? AND block_id=?
        AND source_text=? ORDER BY user_edited DESC,updated_at DESC,rowid DESC LIMIT 1''',
        (source['id'], version['id'], b['id'], b['text'])).fetchone()
    ts = now()
    if row and row['translated_text'] and unit['kind'] in ('metadata','visual'):
        # Reclassification/display policy must not rewrite an earlier successful
        # translation (including user edits). Reports carry the current policy.
        unit['translation_id']=row['id']
        return
    # User-edited/legacy translation content is never replaced automatically.
    if row:
        tid = row['id']
        text = row['translated_text'] if row['user_edited'] else unit.get('text', row['translated_text'])
        provider = row['provider'] if row['user_edited'] else unit.get('provider', row['provider'])
        model = row['model'] if row['user_edited'] else unit.get('model', row['model'])
        con.execute('''UPDATE translations SET translated_text=?,provider=?,model=?,original_page=?,
            original_bbox=?,source_hash=?,runtime=?,prompt_version=?,
            translation_status=?,reason=?,updated_at=? WHERE id=?''',
            (text, provider, model, b['page'], b['bbox_json'], version['content_hash'],
             unit.get('runtime', row['runtime']),
             row['prompt_version'] if row['user_edited'] else unit.get('prompt_version', row['prompt_version']),
             unit['status'], unit['reason'], ts, tid))
    else:
        tid = new_id()
        con.execute('''INSERT INTO translations(id,source_id,version_id,block_id,source_text,translated_text,
            provider,model,original_page,original_bbox,source_hash,runtime,prompt_version,
            translation_status,reason,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (tid, source['id'], version['id'], b['id'], b['text'], unit.get('text', ''),
             unit.get('provider'), unit.get('model'), b['page'], b['bbox_json'], version['content_hash'],
             unit.get('runtime'), unit.get('prompt_version'), unit['status'], unit['reason'], ts, ts))
    unit['translation_id'] = tid


def translate_units(runtime, source, version, units, *, stats=None):
    stats = stats if stats is not None else {}
    stats.update(runtime_calls=0, split_batches=0, attempt_errors={}, stopped_by=None)
    started = time.monotonic()
    deadline = started + MAX_TRANSLATION_SECONDS
    provider = RuntimeProvider(runtime)
    pending = []
    with closing(get_db()) as con:
        for unit in units:
            if unit['status'] != 'pending': continue
            unit.update(translation_candidate=True, translation_attempts=0, translation_error=None)
            b = unit['block']
            row = cached_translation(con, source, version, b)
            originals=unit.get('original_blocks',[b])
            partial_edit=(not row or not row['user_edited']) and any(con.execute('''SELECT 1 FROM translations
                WHERE source_id=? AND version_id=? AND block_id=? AND user_edited=1 AND translated_text<>'' ''',
                (source['id'],version['id'],original['id'])).fetchone() for original in originals if original['id']!=b['id'])
            if partial_edit:
                unit.update(status='failed',reason='assembled_user_edit_review',translation_error='assembled_user_edit_review')
                continue
            if row:
                unit.update(text=row['translated_text'], provider=row['provider'], model=row['model'],
                            runtime=row['runtime'], prompt_version=row['prompt_version'], cached=True,
                            translation_succeeded=True)
            else:
                # A partial user edit cannot become a whole-group translation,
                # nor may a new AI group silently supersede that edit. Keep it
                # untouched and render the full original group for review.
                pending.append(unit)
    batches, batch, chars = [], [], 0
    for unit in pending:
        size = len(unit['block']['text'])
        if batch and (len(batch) >= MAX_BATCH_BLOCKS or chars + size > MAX_BATCH_CHARS):
            batches.append(batch); batch, chars = [], 0
        batch.append(unit); chars += size
    if batch: batches.append(batch)
    if len(batches) > MAX_BATCHES:
        raise PDFError('too_many_batches_select_fewer_pages')
    prompt = (PROMPTS_DIR / 'japanese_pdf_v0_3.md').read_text(encoding='utf-8')
    instructions = prompt + '\nJSON schema:\n' + canonical(TranslationOutput.model_json_schema())
    queue = deque(batches)
    attempt_errors = Counter()
    while queue:
        unresolved = queue.popleft()
        for attempt in range(MAX_ATTEMPTS):
            if time.monotonic() >= deadline:
                stats['stopped_by'] = 'translation_time_limit'; break
            if stats['runtime_calls'] >= MAX_RUNTIME_CALLS:
                stats['stopped_by'] = 'translation_attempt_limit'; break
            active = [u for u in unresolved if u['translation_attempts'] < MAX_UNIT_ATTEMPTS]
            if not active: break
            stats['runtime_calls'] += 1
            for unit in active:
                unit['translation_attempts'] += 1
                unit.update(runtime=provider.name, provider=provider.name, prompt_version=PROMPT_VERSION)
            blocks = [u['block'] for u in active]
            try:
                result = provider.complete_json(instructions, canonical({'blocks': [
                    {'block_id': b['id'], 'text': b['text'],'role':b.get('semantic_role','paragraph')} for b in blocks]}),
                    deadline=min(deadline, time.monotonic()+MAX_TURN_SECONDS))
                for unit in active: unit.update(provider=result.provider, model=result.model)
                valid, errors, anomaly = validate_batch(result.text, blocks)
                if anomaly: attempt_errors[anomaly] += 1
            except TranslationFailure as error:
                valid, errors = {}, {b['id']:error.code for b in blocks}
            except (LLMError, AIError, TimeoutError) as error:
                valid, errors = {}, {b['id']:runtime_failure(error) for b in blocks}
            except (ValueError, TypeError, RecursionError):
                valid, errors = {}, {b['id']:'translation_validation_error' for b in blocks}
            for unit in active:
                bid = unit['block']['id']
                if bid in valid:
                    item = valid[bid]
                    unit.update(text=item.translated_text, provider=result.provider, model=result.model,
                                runtime=provider.name, prompt_version=PROMPT_VERSION,
                                status='translated', reason=None, translation_error=None,
                                translation_succeeded=True)
                    # Commit before requesting another turn: retries/restarts reuse success.
                    with closing(get_db()) as con:
                        save_unit(con, source, version, unit); con.commit()
                    unit['status'] = 'pending'  # still needs safe placement
                else:
                    code = errors[bid]
                    unit.update(translation_error=code, reason=code)
                    attempt_errors[code] += 1
            unresolved = [u for u in unresolved if not u.get('translation_succeeded')]
            if not unresolved: break
        if stats['stopped_by']: break
        # After bounded attempts at this size, isolate unresolved blocks only.
        splittable = [u for u in unresolved if u['translation_attempts'] < MAX_UNIT_ATTEMPTS]
        if len(splittable) > 1:
            middle = len(splittable)//2
            queue.extend((splittable[:middle], splittable[middle:]))
            stats['split_batches'] += 1
    for unit in pending:
        if not unit.get('translation_succeeded'):
            code = unit.get('translation_error') or stats['stopped_by'] or 'translation_attempt_limit'
            unit.update(status='failed', reason=code, translation_error=code)
    stats.update(attempt_errors=dict(attempt_errors), elapsed_seconds=round(time.monotonic()-started, 3))
    return stats
