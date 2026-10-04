"""Bounded two-turn extraction through RuntimeProvider, shared Brief validation."""
from copy import deepcopy
from pathlib import Path
import json
from typing import Annotated
from pydantic import Field, ValidationError
from .ai.compat import RuntimeProvider
from .brief_context import structure, select_context, CAPTION
from .db import now
from .import_bridge.schema import PortableEvidence
from .import_bridge.validator import canonical, _object, _constant
from .llm.base import LLMError
from .paper_brief import (PaperBrief, StrictModel, Identifier, SCHEMA_VERSION,
                          CORE_FIELDS, FIELD_TYPES, brief_fields, effective_status, nested_evidence)
from .source_anchor import resolve_portable_evidence, resolve_source_anchor, occurrences

PROMPT_VERSION = 'paper-brief-extract-0.1'
PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'
MAX_ATTEMPTS = 2  # per stage; at most four turns, no repair agents
MAX_OUTPUT = 1024 * 1024


class GenerationError(ValueError):
    """Fixed public codes only: never include paper, model output or auth payloads."""


class GenerationOutput(StrictModel):
    paper_brief: PaperBrief
    evidence_refs: Annotated[list[PortableEvidence], Field(max_length=512)] = Field(default_factory=list)
    section_ids: Annotated[list[Identifier], Field(max_length=32)] = Field(default_factory=list)


def resolve_output(output, context, mapping, blocks, version):
    """Block reference first; quote/context then geometry via existing resolver."""
    allowed = {b['reference_id'] for b in context['blocks']}
    fields = brief_fields(output.paper_brief)
    needed = {e for n, f in fields.items() for e in f['evidence'] + nested_evidence(n, f)}
    refs = {}
    for ev in output.evidence_refs:
        if ev.evidence_id in refs or ev.evidence_id.startswith('B'):
            raise GenerationError('invalid_evidence')
        if ev.block_id and ev.block_id not in allowed:
            raise GenerationError('unknown_block_reference')
        refs[ev.evidence_id] = ev
    if needed - (allowed | refs.keys()):
        raise GenerationError('unknown_evidence')
    results = fields.get('key_results', {}).get('value') or []
    if len({r['id'] for r in results}) != len(results):
        raise GenerationError('duplicate_result')
    for name in ('important_figures', 'important_tables'):
        if any(not isinstance(item, dict) for item in fields.get(name, {}).get('value') or []):
            raise GenerationError('structured_visual_reference_required')
    resolutions = {}
    for eid in needed:
        ev = refs.get(eid)
        block = mapping[eid] if eid in allowed else mapping.get(ev.block_id)
        if block:
            # Model cannot choose a page/bbox for a locally identified block.
            # A conflicting quote is rejected instead of falling back elsewhere.
            quote = ev.quote if ev and ev.quote else block['text']
            local = resolve_source_anchor({'sourceVersion': version['id'], 'blockId': block['id'], 'quote': quote}, blocks, version)
            if local['status'] != 'resolved' or (ev and (ev.prefix or ev.suffix) and not
                occurrences(block, {'quote': quote, 'prefix': ev.prefix, 'suffix': ev.suffix})):
                raise GenerationError('block_quote_mismatch')
            portable = PortableEvidence(evidence_id=eid, page=block['page'], quote=quote,
                prefix=ev.prefix if ev else None, suffix=ev.suffix if ev else None,
                heading=block.get('heading_path'), source_hash=version['content_hash'], block_id=block['id'])
            anchor = {'type': 'text-quote', 'sourceVersion': version['id'], 'sourceHash': version['content_hash'],
                      'blockId': block['id'], 'blockIdx': block['idx'], 'page': block['page'],
                      'bbox': block.get('bbox'), 'headingPath': block.get('heading_path'), 'quote': quote,
                      'prefix': portable.prefix, 'suffix': portable.suffix}
            resolutions[eid] = {'evidence_id': eid, 'portable': portable.model_dump(mode='json'), **local, 'anchor': anchor}
        else:
            # Do not trust AI-supplied hashes. They are local metadata, not model evidence.
            ev = ev.model_copy(update={'source_hash': None})
            resolutions[eid] = resolve_portable_evidence(ev, blocks, version, source_hash=version['content_hash'])
    for name, f in fields.items():
        requested_status = f['status']
        required = list(dict.fromkeys(f['evidence'] + nested_evidence(name, f)))
        f['status'] = effective_status(f['status'], required, resolutions)
        if name == 'key_results':
            for r in f['value'] or []:
                r['status'] = effective_status(r['status'], r['evidence'], resolutions)
                # Even a derived numeric result requires local evidence before confirmation.
                if not r['evidence']:
                    r['status'] = 'uncertain'
            if any(r['status'] == 'uncertain' for r in f['value'] or []) and f['status'] == 'confirmed':
                f['status'] = 'uncertain'
        f['evidence_resolution'] = [resolutions[e] for e in required]
        f.update(origin='llm', user_edited=False)
        f['provenance'] = {'requested_status': requested_status}
        if name in ('important_figures', 'important_tables'):
            for item in f['value'] or []:
                matched = [resolutions[e]['block'] for e in item['evidence'] if resolutions[e]['status'] == 'resolved']
                caption = next((b for b in matched if CAPTION.match(b['text'].strip()) and
                    CAPTION.match(b['text'].strip()).group(0).casefold() == item['label'].casefold()), None)
                if matched and not caption:
                    raise GenerationError('visual_caption_mismatch')
                if caption:
                    item.update(page=caption['page'], caption=caption['text'])
    return fields


def _stage(provider, prompt_name, context, mapping, blocks, version):
    prompt = (PROMPTS / prompt_name).read_text(encoding='utf-8')
    contract = canonical(GenerationOutput.model_json_schema())
    instructions = prompt + '\nJSON schema (authoritative shared PaperBrief contract):\n' + contract
    for attempt in range(MAX_ATTEMPTS):
        try:
            result = provider.complete_json(instructions, canonical(context))
        except LLMError:
            raise GenerationError('runtime_failed') from None
        try:
            if len(result.text) > MAX_OUTPUT:
                raise GenerationError('output_too_large')
            data = json.loads(result.text, object_pairs_hook=_object, parse_constant=_constant)
            output = GenerationOutput.model_validate(data)
            if set(output.section_ids) - {s['id'] for s in context['section_hierarchy']}:
                raise GenerationError('unknown_section')
            fields = resolve_output(output, context, mapping, blocks, version)
            if context['stage'] == 'classify':
                f = fields['paper_type']
                body_evidence = any(e['status'] == 'resolved' and e['block']['kind'] != 'heading' and
                    e['block']['text'].strip() != context['metadata']['title'].strip() for e in f['evidence_resolution'])
                if f['status'] in ('confirmed', 'derived') and not body_evidence:
                    f['status'] = 'uncertain'
            provenance = {'generated_by': 'knowledge_growth', 'runtime': provider.name,
                'provider': result.provider, 'model': result.model, 'prompt_version': PROMPT_VERSION,
                'generated_at': now(), 'source_version': version['id'], 'schema_version': SCHEMA_VERSION,
                'stage': context['stage'], 'context_coverage': context['coverage']}
            for f in fields.values():
                f['provenance'].update(provenance)
            return output, fields
        except (ValueError, ValidationError, TypeError, RecursionError):
            # Retry is fresh, with no untrusted output echoed into trusted instructions.
            if attempt + 1 == MAX_ATTEMPTS:
                raise GenerationError('invalid_output') from None
            instructions += '\nPrevious response failed validation. Recheck schema, status and only supplied Evidence IDs. Return a fresh JSON object.'


def generate(runtime, source, version, blocks):
    if runtime.status().state != 'ready' or not runtime.capabilities().inference:
        raise GenerationError('ai_disconnected')
    provider = RuntimeProvider(runtime)
    doc, mapping = structure(source, version, blocks)
    first, _ = select_context(doc, stage='classify')
    classification, classified = _stage(provider, 'paper_brief_classify_v0_1.md', first, mapping, blocks, version)
    detail, _ = select_context(doc, stage='extract', selected_sections=classification.section_ids)
    detail['classification_candidates'] = {n: {'value_excerpt': canonical(classified[n]['value'])[:120],
        'status': classified[n]['status']} for n in
        ('paper_type', 'research_objective', 'target_task', 'proposed_method', 'model_family') if n in classified}
    output, extracted = _stage(provider, 'paper_brief_extract_v0_1.md', detail, mapping, blocks, version)
    # Classification evidence and provenance come from its own supplied context.
    extracted['paper_type'] = classified['paper_type']
    # Omitted optional fields are explicit unknowns, never guessed from model knowledge.
    for name in FIELD_TYPES:
        if name not in extracted:
            extracted[name] = {'value': None, 'status': 'not_reported', 'evidence': [],
                'origin': 'llm', 'user_edited': False, 'evidence_resolution': [],
                'provenance': {**extracted['one_line_summary']['provenance'], 'omitted_by_model': True}}
    return extracted, {'classification': first['coverage'], 'extraction': detail['coverage']}


def preview_fields(existing, incoming):
    previous = existing['fields'] if existing else {}
    rows = []
    for name in dict.fromkeys([*CORE_FIELDS, *incoming]):
        if name not in incoming: continue
        field = incoming[name]
        current = previous.get(name)
        if current and (current['origin'] == 'user' or current['user_edited']):
            action = 'preserve_user'
        elif not current:
            action = 'new_field'
        else:
            keys = ('value', 'status', 'evidence', 'origin', 'user_edited')
            same = all(current[k] == field[k] for k in keys)
            same = same and canonical(current['evidence_resolution']) == canonical(field['evidence_resolution'])
            action = 'unchanged' if same else 'generated_update'
        rows.append({'name': name, 'action': action, 'current': current, 'incoming': deepcopy(field)})
    return rows
