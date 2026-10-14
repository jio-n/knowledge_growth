"""Invented TeX prose: native raster identity is not text-reflow identity."""
import json
import re
import fitz
from app.ai.base import TurnEvent
from tests.fixtures.japanese_overlap_factory import make_overlap_fixture,OverlapRuntime,JA,PARTS


def make_recovery_fixture(*,collision=False,graphic=False,clipped=False,tall_envelopes=False):
    with fitz.open(stream=make_overlap_fixture(collision=collision,graphic=graphic),filetype='pdf') as doc:
        # Legitimate, slight PDF text scaling differs from TextWriter's font
        # reconstruction. Broaden descenders so envelope intersections contain
        # a neighbour's upper ink, but never its glyph centre or actual collision.
        for xref in doc[0].get_contents():
            stream=doc.xref_stream(xref)
            if b'/CMR ' in stream:doc.update_stream(xref,stream.replace(b'BT',b'BT\n99.9 Tz'))
        for record in doc[0].get_fonts():
            if record[4]=='CMR':
                xref=record[0];doc.xref_set_key(xref,'BaseFont','/cmr10')
                typ,value=doc.xref_get_key(xref,'DescendantFonts')
                if typ=='array':doc.xref_set_key(int(re.findall(r'\d+',value)[0]),'BaseFont','/cmr10')
        for xref in range(1,doc.xref_length()):
            if doc.xref_get_key(xref,'Type')[1]=='/FontDescriptor' and 'cmr10' in doc.xref_get_key(xref,'FontName')[1].lower():
                doc.xref_set_key(xref,'Descent','-1900' if tall_envelopes else '-1500')
                doc.xref_set_key(xref,'FontBBox','[-100 -1900 1200 1800]' if tall_envelopes else '[-100 -1500 1200 1800]')
        if clipped:
            first=doc[0].get_contents()[0]
            doc.update_stream(first,b'q 44 580 50 15 re W n\n'+doc.xref_stream(first))
            last=doc[0].get_contents()[-1]
            doc.update_stream(last,doc.xref_stream(last)+b'\nQ')
        return doc.tobytes(garbage=4,deflate=True)


class RecoveryRuntime(OverlapRuntime):
    def send_turn(self,session,text,*,hint=None):
        if not self.sessions[session.id].startswith('# japanese-pdf-'):
            yield from super().send_turn(session,text,hint=hint);return
        blocks=json.loads(text)['blocks'];self.inputs.extend(blocks)
        folded=lambda s:re.sub(r'\s+','',s)
        translations={folded(k):v for k,v in JA.items()}
        result=[]
        for b in blocks:
            translated=translations.get(folded(b['text']))
            if self.fail_abstract and folded(b['text']) in {folded(p) for p in PARTS}:translated=None
            result.append(dict(block_id=b['block_id'],translated_text=translated or '',status='translated' if translated else 'uncertain'))
        yield TurnEvent('delta',session.id,'recovery-fixture',json.dumps({'blocks':result},ensure_ascii=False))
        yield TurnEvent('completed',session.id,'recovery-fixture')
