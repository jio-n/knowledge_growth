"""Finite batches, strict IDs and safe reusable TranslationUnits (existing table)."""
from collections import Counter
from contextlib import closing
import json
import re
from typing import Literal
from pydantic import Field, ValidationError
from .ai.compat import RuntimeProvider
from .config import PROMPTS_DIR
from .db import get_db, new_id, now
from .import_bridge.validator import canonical, _object, _constant
from .llm.base import LLMError
from .paper_brief import StrictModel

PROMPT_VERSION = 'japanese-pdf-0.1'
MAX_BATCH_CHARS = 6000
MAX_BLOCK_CHARS = 5000
MAX_BATCH_BLOCKS = 8
MAX_UNITS = 160
MAX_BATCHES = 24  # at most 48 runtime turns per job
MAX_ATTEMPTS = 2


class PDFError(ValueError):
    """Only fixed error codes may cross the API/log boundary."""


class TranslatedBlock(StrictModel):
    block_id: str = Field(min_length=1, max_length=64)
    translated_text: str = Field(max_length=12000)
    status: Literal['translated', 'uncertain']


class TranslationOutput(StrictModel):
    blocks: list[TranslatedBlock] = Field(max_length=MAX_BATCH_BLOCKS)


def validate_reply(text, batch):
    if len(text) > 128000:
        raise ValueError('output_too_large')
    out = TranslationOutput.model_validate(json.loads(text, object_pairs_hook=_object, parse_constant=_constant))
    ids = [b.block_id for b in out.blocks]
    if len(set(ids)) != len(ids) or set(ids) != {b['id'] for b in batch}:
        raise ValueError('block_ids_mismatch')
    original = {b['id']: b['text'] for b in batch}
    for b in out.blocks:
        if b.status == 'translated':
            if not re.search(r'[\u3040-\u30ff\u3400-\u9fff]', b.translated_text):
                raise ValueError('japanese_required')
            # Preserve numerical tokens exactly, including decimal precision and citations.
            tokens = lambda t: Counter(re.findall(r'\d+(?:\.\d+)*', t))
            if tokens(original[b.block_id]) != tokens(b.translated_text):
                raise ValueError('numbers_changed')
        elif b.translated_text:
            raise ValueError('uncertain_must_be_empty')
    return out


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
    # User-edited/legacy translation content is never replaced automatically.
    if row:
        tid = row['id']
        text = row['translated_text'] if row['user_edited'] else unit.get('text', row['translated_text'])
        provider = row['provider'] if row['user_edited'] else unit.get('provider', row['provider'])
        model = row['model'] if row['user_edited'] else unit.get('model', row['model'])
        con.execute('''UPDATE translations SET translated_text=?,provider=?,model=?,original_page=?,
            original_bbox=?,source_hash=?,runtime=?,prompt_version=COALESCE(prompt_version,?),
            translation_status=?,reason=?,updated_at=? WHERE id=?''',
            (text, provider, model, b['page'], b['bbox_json'], version['content_hash'],
             unit.get('runtime', row['runtime']), unit.get('prompt_version'), unit['status'], unit['reason'], ts, tid))
    else:
        tid = new_id()
        con.execute('''INSERT INTO translations(id,source_id,version_id,block_id,source_text,translated_text,
            provider,model,original_page,original_bbox,source_hash,runtime,prompt_version,
            translation_status,reason,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (tid, source['id'], version['id'], b['id'], b['text'], unit.get('text', ''),
             unit.get('provider'), unit.get('model'), b['page'], b['bbox_json'], version['content_hash'],
             unit.get('runtime'), unit.get('prompt_version'), unit['status'], unit['reason'], ts, ts))
    unit['translation_id'] = tid


def translate_units(runtime, source, version, units):
    provider = RuntimeProvider(runtime)
    pending = []
    with closing(get_db()) as con:
        for unit in units:
            if unit['status'] != 'pending': continue
            b = unit['block']
            row = cached_translation(con, source, version, b)
            if row:
                unit.update(text=row['translated_text'], provider=row['provider'], model=row['model'],
                            runtime=row['runtime'], prompt_version=row['prompt_version'], cached=True)
            else:
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
    prompt = (PROMPTS_DIR / 'japanese_pdf_v0_1.md').read_text(encoding='utf-8')
    instructions = prompt + '\nJSON schema:\n' + canonical(TranslationOutput.model_json_schema())
    for batch in batches:
        # A retry includes only failed/uncertain units; successful units are saved immediately.
        unresolved = batch
        for attempt in range(MAX_ATTEMPTS):
            blocks = [u['block'] for u in unresolved]
            try:
                result = provider.complete_json(instructions, canonical({'blocks': [
                    {'block_id': b['id'], 'text': b['text']} for b in blocks]}))
                reply = validate_reply(result.text, blocks)
                mapping = {b.block_id: b for b in reply.blocks}
                remaining = []
                for unit in unresolved:
                    item = mapping[unit['block']['id']]
                    if item.status == 'translated':
                        unit.update(text=item.translated_text, provider=result.provider, model=result.model,
                                    runtime=provider.name, prompt_version=PROMPT_VERSION, status='translated', reason=None)
                        with closing(get_db()) as con:
                            save_unit(con, source, version, unit); con.commit()
                        unit['status'] = 'pending'  # still needs safe placement
                    else:
                        remaining.append(unit)
                unresolved = remaining
            except (ValueError, ValidationError, TypeError, RecursionError, LLMError):
                pass  # fixed error only; never echo untrusted output into the prompt
            if not unresolved: break
        for unit in unresolved:
            unit.update(status='failed', reason='translation_failed')
