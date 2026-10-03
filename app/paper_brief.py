"""Versioned Paper Brief contract; no AI runtime dependency."""
from __future__ import annotations

from typing import Annotated, Generic, Literal, TypeVar
from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

SCHEMA_VERSION = 'paper-brief-0.1'
Status = Literal['confirmed', 'derived', 'uncertain', 'not_reported', 'not_applicable']
Origin = Literal['source', 'llm', 'auto_extract', 'chatgpt_import', 'user']
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$')]
Text = Annotated[str, Field(max_length=20000)]
EvidenceIds = Annotated[list[Identifier], Field(max_length=256)]
T = TypeVar('T')


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class Provenance(StrictModel):
    generated_by: Text | None = None
    model: Text | None = None
    provider: Text | None = None
    prompt_version: Text | None = None
    generated_at: Text | None = None


class BriefField(StrictModel, Generic[T]):
    value: T | None
    status: Status
    evidence: EvidenceIds
    origin: Origin = 'llm'
    user_edited: bool = False
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode='after')
    def unique_refs(self):
        if len(set(self.evidence)) != len(self.evidence):
            raise ValueError('duplicate evidence reference')
        return self


class KeyResult(StrictModel):
    id: Identifier
    dataset: Annotated[str, Field(min_length=1, max_length=2000)]
    task: Annotated[str, Field(min_length=1, max_length=2000)]
    setting: Annotated[str, Field(min_length=1, max_length=2000)]
    metric: Annotated[str, Field(min_length=1, max_length=2000)]
    score: float | int
    unit: Annotated[str, Field(min_length=1, max_length=100)]
    split: Text | None
    comparison: Text | None
    status: Status
    evidence: EvidenceIds

    @model_validator(mode='after')
    def unique_refs(self):
        if self.status == 'confirmed' and not self.evidence:
            raise ValueError('confirmed numeric result requires evidence')
        if len(set(self.evidence)) != len(self.evidence):
            raise ValueError('duplicate evidence reference')
        return self


class Reproducibility(StrictModel):
    code: Text | None = None
    weights: Text | None = None
    data: Text | None = None
    compute: Text | None = None
    license_notes: Text | None = None


PaperType = Literal['method', 'benchmark', 'survey', 'dataset', 'analysis', 'system', 'position', 'other']
CORE_FIELDS = ('paper_type', 'one_line_summary', 'research_objective', 'background',
               'problem', 'target_task', 'target_domain')
_LIST_FIELDS = '''target_task target_domain inputs outputs model_family base_model backbones modalities
trainable_parts frozen_parts learning_regimes supervision adaptation_methods pretraining datasets
evaluation_settings metrics baselines architecture_components architecture_data_flow important_figures
important_tables novelty limitations failure_cases suggested_reading_order ablation'''.split()
_TEXT_FIELDS = '''one_line_summary research_objective background problem proposed_method architecture_summary
model_size inference_requirements training_requirements'''.split()
FIELD_TYPES = {name: BriefField[Annotated[list[Text], Field(max_length=256)]] for name in _LIST_FIELDS}
FIELD_TYPES.update({name: BriefField[Text] for name in _TEXT_FIELDS})
FIELD_TYPES.update(paper_type=BriefField[PaperType], shots=BriefField[Annotated[int, Field(ge=0)]],
                   key_results=BriefField[Annotated[list[KeyResult], Field(max_length=256)]],
                   reproducibility=BriefField[Reproducibility])
PaperBrief = create_model('PaperBrief', __base__=StrictModel,
    schema_version=(Literal['paper-brief-0.1'], ...),
    **{name: (type_, ...) if name in CORE_FIELDS else (type_ | None, None)
       for name, type_ in FIELD_TYPES.items()})


def brief_fields(brief: BaseModel) -> dict:
    return {name: getattr(brief, name).model_dump(mode='json')
            for name in FIELD_TYPES if getattr(brief, name) is not None}


def effective_status(status, refs, resolutions):
    """A claimed confirmed status needs at least one fully resolved evidence ref."""
    if status == 'confirmed' and (not refs or any(resolutions[e]['status'] != 'resolved' for e in refs)):
        return 'uncertain'
    return status
