"""Strict portable package models. Foreign block IDs are hints only."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal
from pydantic import Field, model_validator
from app.paper_brief import (EvidenceIds, Identifier, PaperBrief, Provenance,
                             StrictModel, Text, brief_fields, nested_evidence)

KGPACK_VERSION = 'kgpack-0.1'
REQUIRED_PAYLOADS = {'manifest.json', 'paper_brief.json', 'evidence_refs.json'}
ALLOWED_PAYLOADS = REQUIRED_PAYLOADS | {'knowledge_items.json', 'qa_threads.json'}


class GeometrySpan(StrictModel):
    text: Text
    rect: Annotated[list[float | int], Field(min_length=4, max_length=4)]


class Geometry(StrictModel):
    coordinate_system: Literal['pymupdf_unrotated']
    units: Literal['pt']
    rect: Annotated[list[float | int], Field(min_length=4, max_length=4)]
    page_rect: Annotated[list[float | int], Field(min_length=4, max_length=4)]
    rotation: Literal[0, 90, 180, 270] = 0
    spans: Annotated[list[GeometrySpan], Field(max_length=2048)] = Field(default_factory=list)

    @model_validator(mode='after')
    def bounds(self):
        a, p = self.rect, self.page_rect
        if a[2] <= a[0] or a[3] <= a[1] or p[2] <= p[0] or p[3] <= p[1]:
            raise ValueError('invalid geometry rectangle')
        if a[0] < p[0] or a[1] < p[1] or a[2] > p[2] or a[3] > p[3]:
            raise ValueError('bbox outside page')
        return self


Hash = Annotated[str, Field(pattern=r'^[a-fA-F0-9]{64}$')]


class SourceIdentity(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=2000)]
    authors: Annotated[list[Annotated[str, Field(min_length=1, max_length=1000)]], Field(max_length=256)]
    year: Annotated[int, Field(ge=1000, le=9999)] | None
    doi: Annotated[str, Field(max_length=2000)] | None = None
    arxiv_id: Annotated[str, Field(max_length=2000)] | None = None
    source_hash: Hash | None = None

    @model_validator(mode='after')
    def identifiers(self):
        if self.doi and not re.fullmatch(r'(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?10\.\d{4,9}/\S+', self.doi.strip(), re.I):
            raise ValueError('invalid DOI')
        if self.arxiv_id and not re.fullmatch(r'(?:https?://arxiv\.org/(?:abs|pdf)/|arxiv:\s*)?(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?(?:\.pdf)?', self.arxiv_id.strip(), re.I):
            raise ValueError('invalid arXiv ID')
        return self


class Manifest(StrictModel):
    kgpack_schema_version: Literal['kgpack-0.1']
    package_id: Identifier
    generated_at: Annotated[str, Field(max_length=100)]
    generated_by: Annotated[str, Field(min_length=1, max_length=2000)]
    source_identity: SourceIdentity
    paper_brief_schema_version: Literal['paper-brief-0.1']
    provenance_notice: Annotated[str, Field(min_length=1, max_length=2000)]
    payloads: Annotated[list[str], Field(min_length=2, max_length=4)]

    @model_validator(mode='after')
    def metadata(self):
        date = datetime.fromisoformat(self.generated_at.replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise ValueError('generated_at requires timezone')
        if len(set(self.payloads)) != len(self.payloads):
            raise ValueError('duplicate payload')
        if not {'paper_brief.json', 'evidence_refs.json'} <= set(self.payloads):
            raise ValueError('missing required payload')
        if not set(self.payloads) <= ALLOWED_PAYLOADS - {'manifest.json'}:
            raise ValueError('unexpected payload (PDF/assets unsupported)')
        return self


class PortableEvidence(StrictModel):
    evidence_id: Identifier
    status: Literal['portable'] = 'portable'
    page: Annotated[int, Field(gt=0)] | None = None
    quote: Annotated[str, Field(max_length=20000)] | None = None
    prefix: Annotated[str, Field(max_length=2000)] | None = None
    suffix: Annotated[str, Field(max_length=2000)] | None = None
    heading: Text | None = None
    figure_label: Annotated[str, Field(pattern=r'^(?:Figure|Fig\.)\s+\d+[A-Za-z]?$')] | None = None
    table_label: Annotated[str, Field(pattern=r'^Table\s+\d+[A-Za-z]?$')] | None = None
    bbox: Geometry | None = None
    source_hash: Hash | None = None
    block_id: Identifier | None = None  # deliberately never trusted

    @model_validator(mode='after')
    def selector(self):
        if not (self.page or (self.quote and self.quote.strip()) or self.figure_label or self.table_label):
            raise ValueError('evidence requires a portable selector')
        if self.bbox and not self.page:
            raise ValueError('bbox requires page')
        if (self.prefix or self.suffix) and not (self.quote and self.quote.strip()):
            raise ValueError('context requires quote')
        return self


class KnowledgeCandidate(StrictModel):
    id: Identifier
    section_key: Text
    title: Text | None = None
    content: Text
    evidence: EvidenceIds
    provenance: Provenance = Field(default_factory=Provenance)


class QACandidate(StrictModel):
    id: Identifier
    title: Text
    question: Text
    answer: Text
    evidence: EvidenceIds
    provenance: Provenance = Field(default_factory=Provenance)


class Package(StrictModel):
    manifest: Manifest
    paper_brief: PaperBrief
    evidence_refs: Annotated[list[PortableEvidence], Field(max_length=2048)]
    knowledge_items: Annotated[list[KnowledgeCandidate], Field(max_length=256)] | None = None
    qa_threads: Annotated[list[QACandidate], Field(max_length=256)] | None = None

    @model_validator(mode='after')
    def references(self):
        ids = [ev.evidence_id for ev in self.evidence_refs]
        results = self.paper_brief.key_results
        groups = [ids, [r.id for r in (results.value or [])] if results else [],
                  [r.id for r in self.knowledge_items or []], [r.id for r in self.qa_threads or []]]
        if any(len(g) != len(set(g)) for g in groups):
            raise ValueError('duplicate ID')
        refs = [f['evidence'] for f in brief_fields(self.paper_brief).values()]
        refs += [nested_evidence(n, f) for n, f in brief_fields(self.paper_brief).items()]
        refs += [r.evidence for r in results.value or []] if results else []
        refs += [r.evidence for r in (self.knowledge_items or []) + (self.qa_threads or [])]
        if any(set(ref) - set(ids) for ref in refs):
            raise ValueError('unknown evidence ref')
        for name in ('knowledge_items', 'qa_threads'):
            if (getattr(self, name) is not None) != (name + '.json' in self.manifest.payloads):
                raise ValueError('payload list mismatch')
        return self
