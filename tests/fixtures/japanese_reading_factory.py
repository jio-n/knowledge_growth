"""Invented conference-style paper. No real-paper text or assets."""
import json
from pathlib import Path
import tempfile
import fitz
from app.ai.base import TurnEvent
from app.ai.offline import MockRuntime
from tests.fixtures.pdf_factory import _text

TITLE='READABLE MULTIMODAL RESEARCH WITH ORIGINAL SCIENTIFIC EVIDENCE'
ABSTRACT=('Nowadays, multimodal large language models (MLLM) evaluate 12 tasks with accuracy 91.012345 [1]. '
          'We retain GPT-4o and ViT-B/16 results at https://example.invalid/research. '
          'The setting uses x = 2 while keeping original scientific evidence. '
          'This study reconstructs readable paragraphs without changing numbers or references.')
FALLBACK='Nowadays, multimodal large language models remain readable. This English paragraph is intentionally untranslated.'
FOOTNOTE='1 Code is available at https://example.invalid/code.'
INTRO=['The left column defines the research question and preserves its evidence.',
       'The next left paragraph describes the study and its evaluation.',
       'The right column follows the left column in natural reading order.',
       'The next right paragraph references Figure 1 and Table 1.']
JA={TITLE:'原文の科学的根拠を保持する読みやすいマルチモーダル研究',
    'Abstract':'要旨','1 Introduction':'1 はじめに',
    ABSTRACT:'現在、マルチモーダル大規模言語モデル（MLLM）は12課題を精度91.012345で評価する[1]。'
      'https://example.invalid/research のGPT-4oとViT-B/16の結果を保持する。'
      '設定はx = 2であり、原文の科学的根拠を維持する。本研究では数値や参照を変えずに読みやすい段落を再構成する。',
    INTRO[0]:'左列では研究課題を定義し、その根拠を保持する。',INTRO[1]:'左列の次の段落では研究と評価を説明する。',
    INTRO[2]:'右列は自然な読み順で左列に続く。',INTRO[3]:'右列の次の段落はFigure 1とTable 1を参照する。',
    'Figure 1. Original vector evidence.':'Figure 1. 原文のベクターによる根拠。',
    'Table 1. Original precision.':'Table 1. 原文の小数点精度。'}


def cm_font():
    paths=[Path('/usr/share/fonts/truetype/lyx/cmr10.ttf'),Path('/usr/share/matplotlib/mpl-data/fonts/ttf/cmr10.ttf')]
    found=next((p for p in paths if p.exists()),None)
    if found is None:
        # Portable serif fixture for Windows/minimal CI. The Cloud acceptance
        # run uses real cmr10/cmmi10 when installed, without redistributing them.
        found=Path(tempfile.gettempdir())/'kg-reading-serif-fixture.cff'
        if not found.exists():found.write_bytes(fitz.Font('tiro').buffer)
    return found


def words(page,text,y,*,gap=1.3,size=11,x=52,width=490):
    """Physical word gaps below MuPDF's default synthetic-space threshold."""
    font=fitz.Font(fontfile=str(cm_font()));cursor=x
    for word in text.split(' '):
        math_path=cm_font().with_name('cmmi10.ttf')
        use_math=word=='x' and math_path.exists()
        if use_math:page.insert_font(fontname='CMMI',fontfile=str(math_path))
        length=(fitz.Font(fontfile=str(math_path)) if use_math else font).text_length(word,fontsize=size)
        if cursor+length>x+width:cursor=x;y+=15
        page.insert_text((cursor,y),word,fontname='CMMI' if use_math else 'CMR',fontsize=size)
        cursor+=length+gap
    return y


def make_reading_fixture():
    with fitz.open() as doc:
        p=doc.new_page(width=595,height=842);p.insert_font(fontname='CMR',fontfile=str(cm_font()))
        p.insert_text((52,28),'Published as a conference paper at Synthetic ICLR 2099',fontsize=10)
        p.draw_line((44,38),(551,38),width=.4)
        p.insert_text((52,76),'READABLE MULTIMODAL RESEARCH',fontsize=18,fontname='hebo')
        p.insert_text((52,99),'WITH ORIGINAL SCIENTIFIC EVIDENCE',fontsize=18,fontname='hebo')
        p.insert_text((52,129),'Ada Example 1, Ben Example 2',fontsize=11)
        p.insert_text((52,148),'Synthetic Institute 1 / Research University 2',fontsize=10)
        p.insert_text((52,166),'ada@example.invalid, ben@example.invalid',fontsize=10)
        p.insert_text((52,198),'Abstract',fontname='hebo',fontsize=13)
        end=words(p,ABSTRACT,222)
        # This font run intersects the paragraph's line box, just like inline
        # mathematics in typeset prose. It must not protect the whole paragraph.
        p.insert_text((52,end+44),'1 Introduction',fontname='hebo',fontsize=13)
        words(p,FALLBACK,end+66)
        p.draw_line((52,753),(225,753),width=.3)
        p.insert_text((52,769),FOOTNOTE,fontname='CMR',fontsize=9)
        p.insert_text((290,785),'1',fontname='CMR',fontsize=10)
        q=doc.new_page(width=595,height=842)
        q.insert_text((52,40),'Synthetic research / Running header',fontsize=9)
        for x,content in zip((52,310),(INTRO[:2],INTRO[2:])):
            for y,text in zip((82,175),content):_text(q,(x,y,x+225,y+76),text,size=11)
        q.draw_rect((44,320,551,365),width=.3);q.draw_line((44,340),(551,340),width=.3)
        q.insert_text((60,335),'Vector graph',fontsize=10)
        q.insert_text((52,390),'Figure 1. Original vector evidence.',fontsize=10)
        for y in (445,470,495):q.draw_line((44,y),(551,y),width=.3)
        for x in (44,190,551):q.draw_line((x,445),(x,495),width=.3)
        for x,y,text in [(60,462,'Model'),(200,462,'Accuracy'),(60,487,'GPT-4o'),(200,487,'91.012345')]:q.insert_text((x,y),text,fontsize=10)
        q.insert_text((52,519),'Table 1. Original precision.',fontsize=10)
        q.insert_text((120,610),'y = W x + b (1)',fontsize=13)
        q.insert_text((290,785),'2',fontsize=10)
        return doc.tobytes(garbage=4,deflate=True)


class ReadingRuntime(MockRuntime):
    def __init__(self,fail_abstract=False):super().__init__();self.inputs=[];self.fail_abstract=fail_abstract
    def send_turn(self,session,text,*,hint=None):
        if not self.sessions[session.id].startswith('# japanese-pdf-'):
            yield from super().send_turn(session,text,hint=hint);return
        blocks=json.loads(text)['blocks'];self.inputs.extend(blocks)
        result=[]
        for b in blocks:
            translated=JA.get(b['text'])
            if self.fail_abstract and b['text']==ABSTRACT:translated=None
            result.append(dict(block_id=b['block_id'],translated_text=translated or '',status='translated' if translated else 'uncertain'))
        yield TurnEvent('delta',session.id,'reading-fixture',json.dumps({'blocks':result},ensure_ascii=False))
        yield TurnEvent('completed',session.id,'reading-fixture')
