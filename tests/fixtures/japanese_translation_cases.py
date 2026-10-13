"""Synthetic MMAD-like identifiers/references, not text from the real paper."""
import json
from app.ai.base import AIError, TurnEvent
from app.ai.offline import MockRuntime

TITLE = 'MMAD: A Synthetic Benchmark for GPT-4o and ViT-B/16'
PARAGRAPH = 'We evaluate GPT4o and LoRA on MMAD [1, 2, 7]. Table 2 reports 91.012345% accuracy. Figure 1 shows the method.'
CAPTION = 'Figure 1. A synthetic GPT-4o evaluation on MMAD.'
TRANSLATIONS = {
    TITLE: 'MMAD：GPT-4oとViT-B/16を対象とする合成ベンチマーク',
    PARAGRAPH: 'MMAD [1, 2, 7]でGPT4oとLoRAを評価する。Table 2に正解率91.012345%を示す。Figure 1は手法を示す。',
    CAPTION: 'Figure 1. MMADにおけるGPT-4oの合成評価。',
}


class TranslationFaultRuntime(MockRuntime):
    """All faults are local fake turns; never require auth or a real AI account."""
    def __init__(self, mode='valid', *, bad_text=PARAGRAPH):
        super().__init__()
        self.mode, self.bad_text = mode, bad_text
        self.inputs = []
        self.cancel_requests = []

    def cancel(self, session_id, turn_id):
        self.cancel_requests.append((session_id, turn_id))
        super().cancel(session_id, turn_id)

    def send_turn(self, session, text, *, hint=None):
        if not self.sessions[session.id].startswith('# japanese-pdf-'):
            yield from super().send_turn(session,text,hint=hint); return
        context = json.loads(text)
        self.inputs.append(context)
        yield TurnEvent('started',session.id,'test-turn')
        if self.mode in ('timeout','rpc','runtime','incomplete','cancelled'):
            if self.mode=='incomplete':
                yield TurnEvent('delta',session.id,'test-turn','SECRET_RESPONSE'); return
            if self.mode=='cancelled':
                yield TurnEvent('cancelled',session.id,'test-turn'); return
            raise AIError({'timeout':'turn_timeout','rpc':'malformed_protocol','runtime':'SECRET_CREDENTIAL'}[self.mode])
        rows = [{'block_id':b['block_id'], 'translated_text':TRANSLATIONS.get(b['text'],'日本語の合成検証文章。'), 'status':'translated'} for b in context['blocks']]
        if self.mode=='bad_json' or (self.mode=='split' and len(rows)>1):
            response='SECRET_RESPONSE invalid JSON'
        else:
            for row,b in zip(rows,context['blocks']):
                if b['text'] != self.bad_text: continue
                # "once" variants recover on the next attempt.
                mode = self.mode.removesuffix('_once')
                if self.mode.endswith('_once') and len(self.inputs)>1: continue
                if mode=='numbers': row['translated_text']=row['translated_text'].replace('91.012345','91.0')
                if mode=='uncertain': row.update(translated_text='',status='uncertain')
                if mode=='english': row['translated_text']=b['text']
                if mode=='schema': row['unexpected']='SECRET_RESPONSE'
                if mode=='missing': rows=[r for r in rows if r is not row]; break
                if mode=='duplicate': rows.append(dict(row)); break
                if mode=='invented': row['block_id']='foreign'; break
            response=json.dumps({'blocks':rows},ensure_ascii=False)
            if self.mode=='fenced': response='```json\n'+response+'\n```'
        # Codex's agentMessage deltas can split JSON anywhere, including escapes.
        for pos in range(0,len(response),13):
            yield TurnEvent('delta',session.id,'test-turn',response[pos:pos+13])
        yield TurnEvent('completed',session.id,'test-turn')
