"""Invented dense TeX Abstract: short lines, real math fonts, positive objects."""
import json
import re
from pathlib import Path
import fitz
from app.ai.base import TurnEvent
from tests.fixtures.japanese_overlap_factory import TITLE,TITLE_JA,PARTS,JA,OverlapRuntime
from tests.fixtures.japanese_reading_factory import cm_font,make_reading_fixture

INTRO='The introduction follows the abstract in a single readable flow.'
TRANSLATIONS={**JA,INTRO:'Introductionは要旨に続き、一つの読みやすい流れを保つ。'}


def make_lanes_fixture(*,frame=False,collision=False,unreadable=False,horizontal_scale=1.0):
    with fitz.open() as doc:
        p=doc.new_page(width=792,height=842)
        p.insert_text((44,29),'Published as a conference paper at Synthetic ICLR 2099',fontsize=10)
        p.draw_line((44,39),(748,39),width=.3)
        p.insert_text((44,79),'SYNTHETIC BENCHMARK FOR MULTIMODAL DEFECT',fontname='hebo',fontsize=18)
        p.insert_text((44,104),'ANALYSIS IN MANUFACTURING WITH MLLM MODELS',fontname='hebo',fontsize=18)
        p.insert_text((44,142),'Ada Example 1, Ben Example 2',fontsize=11)
        p.insert_text((44,164),'Synthetic Institute / Research University',fontsize=11)
        p.insert_text((44,185),'ada@example.invalid',fontsize=10)
        p.insert_text((44,216),'Abstract',fontname='hebo',fontsize=13)
        math=cm_font().with_name('cmmi10.ttf');math=math if math.exists() else cm_font()
        names={'R':cm_font(),'M':math};fonts={k:fitz.Font(fontfile=str(v)) for k,v in names.items()}
        for name,path in names.items():
            x=p.insert_font(fontname=name,fontfile=str(path))
            typ,value=doc.xref_get_key(x,'DescendantFonts')
            if typ=='array':
                child=int(re.findall(r'\d+',value)[0]);desc=int(doc.xref_get_key(child,'FontDescriptor')[1].split()[0])
                alias='/cmmi10' if name=='M' and math!=cm_font() else '/cmr10'
                doc.xref_set_key(x,'BaseFont',alias);doc.xref_set_key(child,'BaseFont',alias)
            else:desc=int(doc.xref_get_key(x,'FontDescriptor')[1].split()[0])
            doc.xref_set_key(desc,'Ascent','1200');doc.xref_set_key(desc,'Descent','-700')
            doc.xref_set_key(desc,'FontBBox','[-100 -700 1200 1200]')
        y=246.;line_number=0
        for paragraph in PARTS:
            words=paragraph.split();lines=[]
            while words:
                line=[]
                while words and len(' '.join(line+[words[0]]))<=52:line.append(words.pop(0))
                # Keep each invented paragraph one testable translation unit.
                # This preserves words/punctuation, it does not repair source text.
                if words and line[-1].endswith(('.', ';')):words.insert(0,line.pop())
                lines.append(line)
            for line in lines:
                x=44.
                for word in line:
                    name='M' if re.fullmatch(r'[A-Za-z]+[.,]?',word) else 'R'
                    size=7.2
                    p.insert_text((x,y),word,fontname=name,fontsize=size,render_mode=3 if unreadable and line_number==2 else 0)
                    x+=fonts[name].text_length(word,fontsize=size)+1.4
                if collision and line_number==2:p.draw_line((45,y-2),(x,y-2),width=.4)
                y+=7.782;line_number+=1
            y+=7
        if frame:p.draw_rect((38,229,400,y+4),width=.3)
        p.insert_text((44,y+30),'1 Introduction',fontname='hebo',fontsize=13)
        p.insert_text((44,y+53),INTRO,fontsize=11)
        p.insert_text((390,785),'1',fontsize=10)
        if horizontal_scale!=1:
            for xref in p.get_contents():
                stream=doc.xref_stream(xref)
                if b'/M ' in stream or b'/R ' in stream:doc.update_stream(xref,stream.replace(b'BT',f'BT\n{horizontal_scale*100} Tz'.encode()))
        with fitz.open(stream=make_reading_fixture(),filetype='pdf') as scientific:doc.insert_pdf(scientific,from_page=1,to_page=1)
        return doc.tobytes(garbage=4,deflate=True)


class LanesRuntime(OverlapRuntime):
    def __init__(self,*,mixed=False):
        super().__init__();self.failures={PARTS[2]:'uncertain',PARTS[4]:'uncertain',PARTS[5]:'english'} if mixed else {}
    def send_turn(self,session,text,*,hint=None):
        if not self.sessions[session.id].startswith('# japanese-pdf-'):
            yield from super().send_turn(session,text,hint=hint);return
        blocks=json.loads(text)['blocks'];self.inputs.extend(blocks);rows=[]
        folded=lambda s:re.sub(r'\s+','',s)
        for b in blocks:
            original=next((t for t in TRANSLATIONS if folded(t)==folded(b['text'])),None)
            failure=self.failures.get(original)
            translated=TRANSLATIONS.get(original,'')
            if failure=='english':translated=b['text']
            if failure=='numbers':translated=translated.replace('91.012345','91.012346')
            if failure=='uncertain':translated=''
            rows.append({'block_id':b['block_id'],'translated_text':translated,'status':'translated' if translated else 'uncertain'})
        yield TurnEvent('delta',session.id,'lanes-fixture',json.dumps({'blocks':rows},ensure_ascii=False))
        yield TurnEvent('completed',session.id,'lanes-fixture')
