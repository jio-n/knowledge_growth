"""Invented 1,150-character Abstract; font envelopes overlap, ink does not."""
import json
import re
import fitz
from app.ai.base import TurnEvent
from tests.fixtures.japanese_reading_factory import ReadingRuntime,make_reading_fixture,cm_font,JA as BASE_JA

TITLE='SYNTHETIC BENCHMARK FOR MULTIMODAL DEFECT ANALYSIS IN MANUFACTURING WITH MLLM MODELS'
TITLE_JA='MLLMモデルを用いた製造工程のマルチモーダル欠陥解析のための合成ベンチマーク'
TITLE_OMITTED='MLLMモデルを用いる製造工程向けの合成ベンチマーク'
PARTS=[
 'We introduce a synthetic benchmark for multimodal defect analysis in manufacturing. The study evaluates MLLM models with original visual evidence and provides reproducible tasks for research on reliable inspection.',
 'The benchmark contains 12 generated tasks with accuracy values such as 91.012345. Each example retains its original image, numerical annotation, and citation [1] so that readers can inspect the supporting evidence.',
 'Our method combines language context with visual observations while preserving model identifiers. The comparison uses GPT-4o and ViT-B/16 without modifying their names, settings, or reported decimal precision.',
 'The evaluation separates research observations from generated explanations. We preserve Figure 1 and Table 1 references and keep their captions adjacent to the original objects in the translated reading document.',
 'We assess reading order and the completeness of translated paragraphs across different layouts. Each source unit remains linked to the original page and block identifiers for independent verification of the claims.',
 'The results motivate careful geometric analysis before translation. Font envelopes can overlap even when adjacent glyphs remain legible, so baseline and visible ink must be examined before protecting ordinary prose.',
 'Generalization improves reproducibility [1200].',
]
INTRO='This deliberately untranslated English paragraph retains normal spacing and readable type after the Abstract.'
JA={**BASE_JA,TITLE:TITLE_JA,'Abstract':'要旨','1 Introduction':'1 はじめに',
 PARTS[0]:'製造工程のマルチモーダル欠陥解析のための合成ベンチマークを導入する。MLLMモデルを原文の視覚的根拠で評価し、信頼できる検査の研究に再現可能な課題を提供する。',
 PARTS[1]:'ベンチマークは12個の生成課題を含み、精度の例は91.012345である。各例の原画像、数値注釈、引用[1]を保持し、読者が根拠を確認できるようにする。',
 PARTS[2]:'提案手法はモデル識別子を保持しながら言語文脈と視覚観測を組み合わせる。比較にはGPT-4oとViT-B/16を使用し、名称、設定、小数点精度を変更しない。',
 PARTS[3]:'評価では研究上の観測と生成された説明を分ける。Figure 1とTable 1への参照を維持し、翻訳文書では原物に隣接してCaptionを配置する。',
 PARTS[4]:'異なるレイアウトで段落の読み順と翻訳の完全性を評価する。各原文単位を元ページとblock識別子に対応付け、主張を独立に検証できるようにする。',
 PARTS[5]:'結果は翻訳前の慎重な幾何解析の必要性を示す。隣接glyphが読める場合にもfont外接矩形は重なるため、通常文を保護する前にbaselineと可視文字を確認する。',
 PARTS[6]:'汎化は再現性を改善する[1200]。'}


def make_overlap_fixture(*,collision=False,graphic=False):
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
        font=fitz.Font(fontfile=str(cm_font()));xref=p.insert_font(fontname='CMR',fontfile=str(cm_font()))
        math_file=cm_font().with_name('cmmi10.ttf')
        if math_file.exists():p.insert_font(fontname='CMMI',fontfile=str(math_file))
        for index,text in enumerate(PARTS):
            y=250+index*15 if index<6 else 375
            if collision and index==3:y=250+2*15+1
            assert font.text_length(text,fontsize=7.2)<704
            if index==1:
                cut=text.index('[1]')+1;before,after=text[:cut],text[cut+1:]
                p.insert_text((44,y),before,fontname='CMR',fontsize=7.2)
                x=44+font.text_length(before,fontsize=7.2)
                p.insert_text((x,y-2),'1',fontname='CMR',fontsize=5)
                x+=font.text_length('1',fontsize=5);p.insert_text((x,y),after,fontname='CMR',fontsize=7.2)
            elif index==6 and math_file.exists():
                before,after=text.split('p',1);p.insert_text((44,y),before,fontname='CMR',fontsize=7.2)
                x=44+font.text_length(before,fontsize=7.2)
                p.insert_text((x,y),'p',fontname='CMMI',fontsize=7.2)
                x+=fitz.Font(fontfile=str(math_file)).text_length('p',fontsize=7.2)
                p.insert_text((x,y),after,fontname='CMR',fontsize=7.2)
            else:p.insert_text((44,y),text,fontname='CMR',fontsize=7.2)
        p.insert_text((44,425),'1 Introduction',fontname='hebo',fontsize=13)
        p.insert_textbox((44,450,735,495),INTRO,fontsize=11)
        p.insert_text((390,785),'1',fontsize=10)
        if graphic:p.draw_rect((42,233,749,334),width=.3)
        # PDF font metrics, not outlines, are deliberately broad. Baselines,
        # glyph IDs and original embedded glyph programs remain unchanged.
        typ=doc.xref_get_key(xref,'DescendantFonts')
        if typ[0]=='array':
            descendant=int(re.findall(r'\d+',typ[1])[0])
            descriptor=int(doc.xref_get_key(descendant,'FontDescriptor')[1].split()[0])
        else:descriptor=int(doc.xref_get_key(xref,'FontDescriptor')[1].split()[0])
        doc.xref_set_key(descriptor,'Ascent','1800');doc.xref_set_key(descriptor,'Descent','-900')
        doc.xref_set_key(descriptor,'FontBBox','[-100 -900 1200 1800]')
        with fitz.open(stream=make_reading_fixture(),filetype='pdf') as science:doc.insert_pdf(science,from_page=1,to_page=1)
        return doc.tobytes(garbage=4,deflate=True)


class OverlapRuntime(ReadingRuntime):
    def __init__(self,*,omit_title=False,fail_abstract=False,audit_mode=None):
        super().__init__();self.omit_title=omit_title;self.fail_abstract=fail_abstract;self.reviews=0;self.audit_mode=audit_mode
    def send_turn(self,session,text,*,hint=None):
        system=self.sessions[session.id]
        if system.startswith('# japanese-title-review-'):
            self.reviews+=1;payload=json.loads(text)
            if self.audit_mode=='timeout':
                from app.ai.base import AIError
                raise AIError('turn_timeout')
            answer=title_review_reply(payload)
            if self.audit_mode=='uncertain':answer['verdict']='uncertain'
            if self.audit_mode=='wrong_id':answer['block_id']='invented'
            if self.audit_mode=='missing_check':answer['checks'].pop()
            if self.audit_mode=='fabricated_range':answer['checks'][0]['source_spans'][0]['end']=99999
            if self.audit_mode=='unmapped':answer['checks'][0]['source_spans']=[{'start':0,'end':9}]
            raw='{broken' if self.audit_mode=='malformed' else json.dumps(answer,ensure_ascii=False)
            yield TurnEvent('delta',session.id,'title-fixture',raw)
            yield TurnEvent('completed',session.id,'title-fixture');return
        if not system.startswith('# japanese-pdf-'):
            yield from super().send_turn(session,text,hint=hint);return
        blocks=json.loads(text)['blocks'];self.inputs.extend(blocks);result=[]
        for b in blocks:
            translated=JA.get(b['text'])
            if b['text']==TITLE and self.omit_title:translated=TITLE_OMITTED
            if self.fail_abstract and b['text'] in PARTS:translated=None
            result.append(dict(block_id=b['block_id'],translated_text=translated or '',status='translated' if translated else 'uncertain'))
        yield TurnEvent('delta',session.id,'overlap-fixture',json.dumps({'blocks':result},ensure_ascii=False))
        yield TurnEvent('completed',session.id,'overlap-fixture')


def title_review_reply(payload):
    if payload['original_title']!=TITLE:
        from app.llm.japanese_fixture import mock_title_review
        return mock_title_review(payload)
    checks=[]
    for category,source,target in [
        ('research_subject','MULTIMODAL DEFECT ANALYSIS','マルチモーダル欠陥解析'),
        ('application_domain','MANUFACTURING','製造工程'),
        ('method_model','MLLM MODELS','MLLMモデル'),
        ('research_positioning','SYNTHETIC BENCHMARK','合成ベンチマーク'),
        ('identifiers','MLLM','MLLM')]:
        source_index=TITLE.index(source);target_index=payload['translated_title'].find(target)
        checks.append({'category':category,'status':'covered' if target_index>=0 else 'missing',
                       'source_spans':[{'start':source_index,'end':source_index+len(source)}],
                       'translated_spans':[{'start':target_index,'end':target_index+len(target)}] if target_index>=0 else []})
    return {'block_id':payload['block_id'],'verdict':'complete' if all(c['status']=='covered' for c in checks) else 'incomplete','checks':checks}
