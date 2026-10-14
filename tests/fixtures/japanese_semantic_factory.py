"""Invented three-page paper with split title/columns and real vector evidence."""
import json
import fitz
from app.ai.base import TurnEvent
from app.ai.offline import MockRuntime
from tests.fixtures.pdf_factory import _text

TITLE_LINES=['SYNTHETIC MUL-','TIMODAL RESEARCH WITH FEW-SHOT','EVIDENCE AND READABLE JAPANESE DOCUMENTS']
TITLE='SYNTHETIC MULTIMODAL RESEARCH WITH FEW-SHOT EVIDENCE AND READABLE JAPANESE DOCUMENTS'
ABSTRACT_PARTS=['This synthetic study assembles split text into complete reading units',
                'and keeps meaning, numbers, citations [1], and original evidence intact.']
ABSTRACT_BODY=' '.join(ABSTRACT_PARTS)
ABSTRACT_SECOND='The proposed pipeline preserves Figure 1 and Table 1 while translating normal paragraphs.'
UNTRANSLATED='This intentionally untranslated English paragraph remains readable in its correct place. It must not become a miniature image or interrupt the research reading order.'
INTRO=[
 'The first left-column paragraph defines the research question and its original evidence.',
 'The second left-column paragraph explains why complete semantic units improve reading.',
 'The third left-column paragraph closes the left column before the right column begins.',
 'The first right-column paragraph follows every paragraph in the left column.',
 'The second right-column paragraph introduces Figure 1 without changing its reference.',
 'The method paragraph preserves Table 1, GPT-4o, and accuracy 91.012345.']
CAPTION='Figure 1. Synthetic research pipeline with unchanged labels.'
TABLE_PARTS=['Table 1. Synthetic scores with unchanged precision','and unchanged model names.']
TABLE_CAPTION=' '.join(TABLE_PARTS)
ARXIV='arXiv:0000.00000v1 [test] 1 Jan 2099'
CONFERENCE='Proceedings of the Synthetic Research Conference 2099'
JA={TITLE:'FEW-SHOTの根拠を保持する合成マルチモーダル研究と読みやすい日本語文書',
 'Abstract':'要旨',ABSTRACT_BODY:'本合成研究では、分断された文章を完全な読書単位にまとめ、意味、数値、引用[1]、原文の根拠を保持する。',
 ABSTRACT_SECOND:'提案パイプラインは通常の段落を翻訳しながら、Figure 1とTable 1を保持する。',
 '1 Introduction':'1 はじめに','2 Method':'2 手法','3 Evaluation':'3 評価',
 INTRO[0]:'左列の最初の段落では、研究課題と原文の根拠を定義する。',
 INTRO[1]:'左列の次の段落では、完全な意味単位が読みやすさを改善する理由を説明する。',
 INTRO[2]:'左列の最後の段落で左列を終え、その後に右列を読み始める。',
 INTRO[3]:'右列の最初の段落は、左列のすべての段落に続く。',
 INTRO[4]:'右列の次の段落では、参照を変えずにFigure 1を導入する。',
 INTRO[5]:'手法の段落では、Table 1、GPT-4o、精度91.012345を保持する。',
 CAPTION:'Figure 1. ラベルを保持した合成研究のパイプライン。',
 TABLE_CAPTION:'Table 1. 小数点精度とモデル名を保持した合成スコア。'}


def make_semantic_fixture():
    with fitz.open() as doc:
        p=doc.new_page(width=595,height=842)
        for y,text in zip((48,72,96),TITLE_LINES):p.insert_text((44,y),text,fontname='hebo',fontsize=18)
        p.insert_text((44,134),'Ada Example',fontsize=10)
        p.insert_text((105,128),'1',fontsize=7)
        p.insert_text((150,134),'Ben Example',fontsize=10)
        p.insert_text((212,128),'2',fontsize=7)
        p.insert_text((44,156),'Synthetic Institute 1, Research University 2',fontsize=9)
        p.insert_text((44,176),'ada@example.invalid, ben@example.invalid',fontsize=9)
        p.insert_text((44,196),CONFERENCE,fontsize=9)
        p.insert_text((16,715),ARXIV,rotate=90,fontsize=9)
        p.insert_text((44,231),'Abstract',fontname='hebo',fontsize=14)
        for y,text in zip((255,273),ABSTRACT_PARTS):p.insert_text((44,y),text,fontsize=11)
        _text(p,(44,300,550,350),ABSTRACT_SECOND,size=11)
        for x,label in ((44,'Input'),(235,'Encoder'),(426,'Output')):
            p.draw_rect((x,390,x+125,450),color=(0,0,0),fill=(.93,.96,1),width=.4)
            p.insert_text((x+12,424),label,fontsize=10)
        p.draw_line((169,420),(235,420),width=.4);p.draw_line((360,420),(426,420),width=.4)
        _text(p,(44,470,551,505),CAPTION,size=10)
        _text(p,(44,535,551,625),UNTRANSLATED,size=11)
        p.insert_text((145,695),'y = W x + b (1)',fontsize=14)
        p.insert_text((290,820),'1',fontsize=9)
        q=doc.new_page(width=595,height=842)
        q.insert_text((44,50),'1 Introduction',fontname='hebo',fontsize=16)
        for y,text in zip((100,275,470),INTRO[:3]):_text(q,(44,y,283,y+125),text,size=11)
        for y,text in zip((100,275),INTRO[3:5]):_text(q,(312,y,551,y+125),text,size=11)
        q.insert_text((312,430),'2 Method',fontname='hebo',fontsize=16)
        _text(q,(312,470,551,620),INTRO[5],size=11)
        q.insert_text((44,22),'Synthetic Research / Running header',fontsize=8)
        q.insert_text((290,820),'2',fontsize=9)
        table=doc.new_page(width=595,height=842)
        table.insert_text((44,50),'3 Evaluation',fontname='hebo',fontsize=16)
        for y in (115,145,175,205):table.draw_line((44,y),(551,y),width=.3)
        for x in (44,220,370,551):table.draw_line((x,115),(x,205),width=.3)
        for row,cells in enumerate([['Model','Accuracy','Time'],['GPT-4o','91.012345','0.123456'],['ViT-B/16','92.987654','0.345678']]):
            for x,text in zip((52,228,378),cells):table.insert_text((x,134+row*30),text,fontsize=10)
        for y,text in zip((230,246),TABLE_PARTS):table.insert_text((44,y),text,fontsize=10)
        table.insert_text((290,820),'3',fontsize=9)
        return doc.tobytes(garbage=4,deflate=True)


class SemanticRuntime(MockRuntime):
    def __init__(self,*,fail_title=False):super().__init__();self.inputs=[];self.fail_title=fail_title
    def send_turn(self,session,text,*,hint=None):
        if not self.sessions[session.id].startswith('# japanese-pdf-'):
            yield from super().send_turn(session,text,hint=hint);return
        blocks=json.loads(text)['blocks'];self.inputs.append(blocks)
        output=[]
        for b in blocks:
            translated=JA.get(b['text'])
            if self.fail_title and b['text']==TITLE:translated=None
            output.append({'block_id':b['block_id'],'translated_text':translated or '', 'status':'translated' if translated else 'uncertain'})
        yield TurnEvent('delta',session.id,'semantic-fixture',json.dumps({'blocks':output},ensure_ascii=False))
        yield TurnEvent('completed',session.id,'semantic-fixture')
