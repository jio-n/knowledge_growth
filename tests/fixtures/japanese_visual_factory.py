"""Portable embedded-font synthetic paper, no real-paper text or font file."""
import json
import fitz
from app.ai.base import TurnEvent
from app.ai.offline import MockRuntime
from tests.fixtures.pdf_factory import _text

TITLE='Synthetic Multimodal Benchmark'
AFFILIATION='Institute 1, Synthetic Research'
BODY='We study a synthetic method and preserve original evidence.'
INTRO='The synthetic method uses generated data in a few-shot setting.'
UNCERTAIN='A synthetic unresolved paragraph is retained in its original form.'
FIGURE='Figure 1. Synthetic input-encoder-output architecture.'
TABLE='Table 1. Synthetic benchmark scores (invented test values).'
JA={AFFILIATION:'研究機関1、合成研究', 'Abstract':'概要',
    BODY:'本研究では合成手法を検討し、原文の根拠を保持する。',
    INTRO:'この合成手法はFew-shot設定で生成データを使用する。',
    FIGURE:'Figure 1. 合成手法の入力・エンコーダ・出力構成。',
    TABLE:'Table 1. 合成ベンチマークのスコア（検証用の架空値）。'}


def make_visual_fixture(*,rotation=0,crop=False):
    with fitz.open() as doc:
        p=doc.new_page(width=595,height=842)
        # Same Apache 2.0 Droid font already embedded by the application. The
        # original TrueType program reproduces DocumentWriter's font conversion.
        p.insert_font(fontname='EmbeddedFixture',fontbuffer=fitz.Font('cjk').buffer)
        p.insert_text((40.137,60.213),TITLE,fontsize=26,fontname='EmbeddedFixture')
        p.insert_text((40,94),'Synthetic Author',fontsize=12,fontname='EmbeddedFixture')
        p.insert_text((140,90),'1',fontsize=7,fontname='EmbeddedFixture')
        _text(p,(40,115,555,145),AFFILIATION)
        _text(p,(40,155,555,195),'Abstract',size=18,bold=True)
        _text(p,(40,200,555,255),BODY)
        _text(p,(40,270,555,330),INTRO)
        p.draw_rect((40,360,550,455),color=(0,0,0),fill=(.94,.97,1))
        for row in range(2):
            for col in range(5):
                p.insert_text((50+col*99,384+row*45),f'Model-{row*5+col:02d}',fontsize=8,fontname='EmbeddedFixture')
        pix=fitz.Pixmap(fitz.csRGB,(0,0,20,20),False);pix.clear_with(120)
        p.insert_image((270,395,300,425),stream=pix.tobytes('png'))
        _text(p,(40,470,555,495),FIGURE)
        for y in (525,545,565,585):p.draw_line((40,y),(550,y))
        for x in (40,200,320,430,550):p.draw_line((x,525),(x,585))
        cells=[['Model','Metric','Score','Time'],['GPT-4o','AUROC','91.012345','0.123456'],['ViT-B/16','Accuracy','92.987654','0.345678']]
        for row,line in enumerate(cells):
            for x,text in zip((45,205,325,435),line):
                p.insert_text((x,538+row*20),text,fontname='EmbeddedFixture',fontsize=8)
        _text(p,(40,600,555,625),TABLE)
        p.insert_text((100,660),'y = W x + b (1)',fontsize=14,fontname='EmbeddedFixture')
        _text(p,(40,715,555,760),UNCERTAIN)
        if crop:p.set_cropbox((20,20,575,822))
        p.set_rotation(rotation)
        return doc.tobytes(garbage=4,deflate=True)


class VisualFaultRuntime(MockRuntime):
    def __init__(self,*,all_translated=False):
        super().__init__();self.inputs=[];self.all_translated=all_translated
    def send_turn(self,session,text,*,hint=None):
        if not self.sessions[session.id].startswith('# japanese-pdf-0.2'):
            yield from super().send_turn(session,text,hint=hint);return
        blocks=json.loads(text)['blocks'];self.inputs.append(blocks)
        rows=[]
        for b in blocks:
            original=b['text'];translated=JA.get(original)
            if original==TITLE:translated='合成マルチモーダル・ベンチマーク' if self.all_translated else TITLE
            if original==UNCERTAIN:translated='合成の未解決段落は元の形式で保持する。' if self.all_translated else None
            rows.append({'block_id':b['block_id'],'translated_text':translated or '', 'status':'translated' if translated else 'uncertain'})
        yield TurnEvent('delta',session.id,'fixture-turn',json.dumps({'blocks':rows},ensure_ascii=False))
        yield TurnEvent('completed',session.id,'fixture-turn')
