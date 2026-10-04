"""Fake AIRuntime using generated paper statements; no RPC/account/API keys."""
import json
from app.ai.offline import MockRuntime
from app.ai.base import TurnEvent
from app.paper_brief import CORE_FIELDS, SCHEMA_VERSION
import fitz


def paper_pdf(paper_type='method'):
    with fitz.open() as doc:
        doc.set_metadata({'title': 'Generated AI Research', 'author': 'Fixture Author', 'creationDate': 'D:20261004000000Z'})
        for title, text in [
            ('Abstract', f'This {paper_type} paper studies synthetic classification. We evaluate a frozen backbone and a trainable LoRA adapter.'),
            ('1 Method', 'A vision language model uses text and image modalities. We report few-shot evaluation, supervised adaptation and PEFT via LoRA. The backbone is frozen. Model size is not specified.'),
            ('2 Evaluation', 'Synthetic-00 classification: few-shot, Accuracy 62%, test split. Baseline Accuracy 60%. Limitations: only synthetic data. No failure case analysis is reported.'),
            ('3 Architecture', 'Figure 1. Text and image encoder to LoRA adapter to classification output.\nTable 1. Synthetic classification test scores: Accuracy 62% (few-shot).')]:
            p = doc.new_page()
            p.insert_text((40, 45), title, fontsize=18, fontname='hebo')
            p.insert_textbox(fitz.Rect(40, 90, 555, 250), text, fontsize=11)
        return doc.tobytes()


def field(value=None, status='not_reported', evidence=()):
    return {'value': value, 'status': status, 'evidence': list(evidence)}


def response(context, paper_type='method'):
    blocks = context['blocks']
    ref = lambda needle: next(b['reference_id'] for b in blocks if needle in b['text'])
    abstract, method, result = ref('This '), ref('Model size'), ref('Baseline Accuracy')
    data = {n: field() for n in CORE_FIELDS}
    data.update(paper_type=field(paper_type, 'confirmed', [abstract]),
        one_line_summary=field('Synthetic classification study', 'derived', [abstract]),
        research_objective=field('Study synthetic classification', 'confirmed', [abstract]),
        target_task=field(['classification'], 'confirmed', [abstract]),
        target_domain=field(['synthetic'], 'confirmed', [abstract]))
    if context['stage'] == 'extract':
        data.update(model_family=field(['VLM'], 'confirmed', [method]),
            model_size=field(), shots=field(), failure_cases=field(status='uncertain'),
            proposed_method=field(status='not_applicable') if paper_type in ('benchmark', 'survey') else field('LoRA adapter', 'confirmed', [method]),
            learning_regimes=field(['few-shot', 'supervised', 'PEFT', 'LoRA', 'frozen backbone'], 'confirmed', [method]),
            architecture_components=field(['encoder', 'adapter', 'output'], 'derived', [method]),
            key_results=field([{'id': 'r1', 'dataset': 'Synthetic-00', 'task': 'classification', 'setting': 'few-shot',
                'metric': 'Accuracy', 'score': 62, 'unit': '%', 'split': 'test', 'comparison': 'Baseline 60%',
                'status': 'confirmed', 'evidence': [result]}], 'confirmed', [result]))
        for name, label in [('important_figures', 'Figure 1'), ('important_tables', 'Table 1')]:
            b = next(b for b in blocks if b['text'].startswith(label))
            data[name] = field([{'label': label, 'page': b['page'], 'caption': b['text'], 'evidence': [b['reference_id']]}], 'confirmed', [b['reference_id']])
    return {'paper_brief': {'schema_version': SCHEMA_VERSION, **data}, 'evidence_refs': [],
            'section_ids': [s['id'] for s in context['section_hierarchy'][:4]] if context['stage'] == 'classify' else []}


class FakeBriefRuntime(MockRuntime):
    def __init__(self, paper_type='method', mutate=None, invalid_attempts=0):
        super().__init__()
        self.paper_type = paper_type
        self.mutate = mutate
        self.invalid_attempts = invalid_attempts
        self.calls = []
    def send_turn(self, session, text, *, hint=None):
        context = json.loads(text)
        self.calls.append({'instructions': self.sessions[session.id], 'context': context})
        data = response(context, self.paper_type)
        if self.mutate: self.mutate(data, context)
        reply = json.dumps(data)
        if self.invalid_attempts:
            self.invalid_attempts -= 1
            reply = 'not JSON PRIVATE_OUTPUT_SENTINEL'
        yield TurnEvent('started', session.id, 'turn')
        yield TurnEvent('delta', session.id, 'turn', reply)
        yield TurnEvent('completed', session.id, 'turn')
