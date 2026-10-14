"""End-to-end generated fixtures, geometry/pixels, cache/retry and lifecycle."""
from contextlib import closing
import hashlib
import json
import fitz
import pytest
from fastapi.testclient import TestClient
from app.ai.offline import MockRuntime, NoAIRuntime
from app.ai.base import TurnEvent
from app.config import files_dir, generated_dir
from app.db import get_db
from app.main import create_app
from app.japanese_pdf import norm
from app.japanese_translation import validate_reply
from tests.fixtures.japanese_pdf_factory import make_japanese_fixture, BODY


def upload(client, data):
    r = client.post('/api/sources/pdf', files={'file': ('generated.pdf', data, 'application/pdf')}, data={'force': 'true'})
    assert r.status_code == 200, r.text
    return r.json()['source']['id']


def generate(client, sid, pages, **kwargs):
    r = client.post(f'/api/sources/{sid}/japanese-pdf/generate', json={'pages': pages, **kwargs})
    assert r.status_code == 202, r.text
    status = client.get(f'/api/sources/{sid}/japanese-pdf').json()
    return status, client.get(f'/api/sources/{sid}/japanese-pdf/{status["generation_id"]}/file')


@pytest.mark.parametrize('kind,pages', [('simple',[1]),('two_column',[1]),('reader_detail',[1,2]),('layout',[1,2,3]),('reflow_demo',[1,2,3])])
def test_reflow_content_visuals_mapping_and_original_hash(client,kind,pages):
    original=make_japanese_fixture(kind); sid=upload(client,original)
    status,response=generate(client,sid,pages)
    assert status['state']=='preview_ready',status
    report=status['report']; assert report['layout_version']=='reflow-3'
    assert report['quality']['missing_or_duplicate']==report['quality']['overlaps']==report['quality']['outside_page']==0
    assert report['quality']['reading_order_preserved']
    with closing(get_db()) as con:
        src=con.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone()
        assert (files_dir()/src['file_path']).read_bytes()==original
        rows={r['block_id']:r for r in con.execute('SELECT * FROM translations WHERE source_id=?',(sid,))}
        assert all(r['version_id'] and r['original_bbox'] and r['source_hash']==hashlib.sha256(original).hexdigest() for r in rows.values())
    with fitz.open(stream=response.content,filetype='pdf') as output:
        assert len(output)==report['japanese_page_count']
        assert output.get_toc() and all(1<=item[2]<=len(output) for item in output.get_toc())
        assert '□' not in ''.join(p.get_text() for p in output) and '�' not in ''.join(p.get_text() for p in output)
        for unit in report['units']:
            assert unit['placements'] and unit['section'] is not None
            if unit['status']=='translated':
                extracted=''.join(output[p['japanese_page']-1].get_text('text',clip=fitz.Rect(p['japanese_bbox']['rect'])) for p in unit['placements'])
                assert norm(extracted)==norm(rows[unit['block_id']]['translated_text'])
                assert unit['font_size']>=10
        for asset in report['assets']:
            assert asset['vector_preserved']
            if asset['kind'] in ('figure','table') and kind in ('reflow_demo','reader_detail'):
                assert output[asset['japanese_page']-1].get_drawings()
            for bid in asset['caption_block_ids']:
                caption=next(u for u in report['units'] if u['block_id']==bid)
                assert all(p['japanese_page']==asset['japanese_page'] for p in caption['placements'])
        if kind in ('reader_detail','reflow_demo'):
            assert '66.012345' in ''.join(p.get_text() for p in output)
            assert {'figure','table','equation'} <= {a['kind'] for a in report['assets']}
    assert all(q['source_text_preserved'] and q['native_size_preserved'] and q['mean_pixel_difference']<=.2 for q in report['quality']['visuals'])


@pytest.mark.parametrize('rotation',[90,180,270])
def test_rotated_cropbox_source_geometry_kept_and_visuals_reflow(client,rotation):
    data=make_japanese_fixture('reflow_demo',rotation=rotation,crop=True)
    sid=upload(client,data); status,r=generate(client,sid,[1,2,3])
    assert status['state']=='preview_ready',status
    assert any(u['bbox']['rotation']==rotation for u in status['report']['units'] if u['page']==1)
    assert client.get(f'/api/sources/{sid}/file').content==data
    with fitz.open(stream=r.content,filetype='pdf') as result: assert all(p.rotation==0 for p in result)


@pytest.mark.parametrize('kind',['near','overlap','symbol','unruled'])
def test_ambiguous_or_protected_display_is_retained_and_mapped(client,kind):
    data=make_japanese_fixture(kind);sid=upload(client,data)
    status,r=generate(client,sid,[1])
    assert status['state']=='preview_ready',status
    assert status['report']['assets']
    assert all(q['mean_pixel_difference']<=.2 for q in status['report']['quality']['visuals'])
    if kind=='unruled':
        with fitz.open(stream=r.content,filetype='pdf') as doc:
            text=''.join(p.get_text() for p in doc)
            assert 'SyntheticGPT' in text and 'LoRA-ViT' in text and '91.012345' in text


def test_no_text_cannot_claim_ready(client):
    data=make_japanese_fixture('no_text'); sid=upload(client,data)
    status,r=generate(client,sid,[1])
    assert status['state']=='failed' and status['error']=='no_safe_translated_blocks'
    assert r.status_code==404 and client.get(f'/api/sources/{sid}/file').content==data


@pytest.mark.parametrize('pages', [[],[1,2,3,4],[0],[1,1],[1.5],['1'],[True],[99]])
def test_page_validation(client,pages):
    sid=upload(client,make_japanese_fixture())
    assert client.post(f'/api/sources/{sid}/japanese-pdf/generate',json={'pages':pages}).status_code==422


def test_noncontiguous_cache_download_restart_and_tamper(client):
    sid=upload(client,make_japanese_fixture())
    status,r=generate(client,sid,[1,3]); token=status['generation_id']
    assert status['state']=='preview_ready' and status['pages']==[1,3]
    duplicate=client.post(f'/api/sources/{sid}/japanese-pdf/generate',json={'pages':[3,1]}).json()
    assert duplicate['generation_id']==token
    download=client.get(f'/api/sources/{sid}/japanese-pdf/{token}/file?download=true')
    assert download.content==r.content and 'attachment' in download.headers['content-disposition']
    with TestClient(create_app(runtime=NoAIRuntime())) as reopened:
        assert reopened.get(f'/api/sources/{sid}/japanese-pdf').json()['state']=='preview_ready'
        assert reopened.get(f'/api/sources/{sid}/japanese-pdf/{token}/file').content==r.content
        assert reopened.post(f'/api/sources/{sid}/japanese-pdf/generate',json={'pages':[1,3]}).json()['generation_id']==token
    (generated_dir()/f'{token}.pdf').write_bytes(b'corrupt')
    assert client.get(f'/api/sources/{sid}/japanese-pdf/{token}/file').status_code==409
    assert client.get(f'/api/sources/{sid}/japanese-pdf').json()['state']=='failed'
    # Existing preview history survives a failed later generation.
    client.post(f'/api/sources/{sid}/japanese-pdf/generate',json={'pages':[1,3],'retry_failed':True})
    assert client.get(f'/api/sources/{sid}/japanese-pdf').json()['state']=='preview_ready'


@pytest.mark.parametrize('change', ['hash','version','blocks','file'])
def test_stale_source_is_not_latest_or_downloadable(client,change):
    sid=upload(client,make_japanese_fixture('simple')); status,_=generate(client,sid,[1]); token=status['generation_id']
    with closing(get_db()) as con:
        src=con.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone()
        if change=='hash': con.execute("UPDATE sources SET content_hash='changed' WHERE id=?",(sid,))
        elif change=='version': con.execute("INSERT INTO source_versions(id,source_id,content_hash,raw_path,fetched_at) VALUES('new',?,?,?,'9999')",(sid,src['content_hash'],src['file_path']))
        elif change=='blocks': con.execute("UPDATE document_blocks SET text=text || ' changed' WHERE source_id=?",(sid,))
        else: (files_dir()/src['file_path']).write_bytes(make_japanese_fixture('two_column'))
        con.commit()
    assert client.get(f'/api/sources/{sid}/japanese-pdf').json()['state']=='not_generated'
    assert client.get(f'/api/sources/{sid}/japanese-pdf/{token}/file').status_code==409


@pytest.mark.parametrize('bad', ['invalid_json','missing','duplicate','invented','extra','numbers','uncertain','english','duplicate_key'])
def test_json_response_rejects_wrong_contract(bad):
    batch=[{'id':'a','text':'Figure 1. Accuracy 91.012345%.'}]
    reply={'blocks':[{'block_id':'a','translated_text':'Figure 1. Accuracyは91.012345%。','status':'translated'}]}
    if bad=='invalid_json': text='not JSON'
    elif bad=='duplicate_key': text='{"blocks":[],"blocks":[]}'
    else:
        if bad=='missing': reply['blocks']=[]
        if bad=='duplicate': reply['blocks']*=2
        if bad=='invented': reply['blocks'][0]['block_id']='fiction'
        if bad=='extra': reply['other']='unexpected'
        if bad=='numbers': reply['blocks'][0]['translated_text']='Figure 1. Accuracyは99%。'
        if bad=='uncertain': reply['blocks'][0]['status']='uncertain'
        if bad=='english': reply['blocks'][0]['translated_text']='Figure 1. Accuracy 91.012345%.'
        text=json.dumps(reply)
    with pytest.raises(ValueError): validate_reply(text,batch)


class FaultRuntime(MockRuntime):
    def __init__(self,mode): super().__init__(); self.mode=mode; self.inputs=[]
    def send_turn(self,session,text,*,hint=None):
        if not self.sessions[session.id].startswith('# japanese-pdf-'):
            yield from super().send_turn(session,text,hint=hint); return
        context=json.loads(text); self.inputs.append(context)
        if self.mode=='overflow':
            from app.llm.japanese_fixture import fixture_reply
            reply=fixture_reply(context)
            for b,original in zip(reply['blocks'],context['blocks']):
                if original['text']==BODY: b['translated_text']='非常に長い日本語文章。'*1000
        elif self.mode=='missing_glyph':
            from app.llm.japanese_fixture import fixture_reply
            reply=fixture_reply(context)
            for b,original in zip(reply['blocks'],context['blocks']):
                if original['text']==BODY: b['translated_text']='日本語の欠字🦄'
        elif self.mode=='bad': reply={'blocks':[]}
        else:
            from app.llm.japanese_fixture import fixture_reply
            reply=fixture_reply(context)
            if len(self.inputs)==1:
                reply['blocks'][-1].update(status='uncertain',translated_text='')
        yield TurnEvent('delta',session.id,'t',json.dumps(reply,ensure_ascii=False))
        yield TurnEvent('completed',session.id,'t')


def test_long_japanese_flows_to_more_pages_without_shrinking_and_reuses_cache(client):
    runtime=FaultRuntime('overflow');client.app.state.ai_runtime=runtime
    sid=upload(client,make_japanese_fixture('overflow'));status,r=generate(client,sid,[1])
    assert status['state']=='preview_ready',status
    assert status['report']['japanese_page_count']>status['report']['original_page_count']
    assert not status['report']['counts'].get('overflow')
    body=next(u for u in status['report']['units'] if len(u['placements'])>1)
    assert body['status']=='translated' and body['font_size']==11
    with fitz.open(stream=r.content,filetype='pdf') as doc:
        text=''.join(doc[p['japanese_page']-1].get_text('text',clip=fitz.Rect(p['japanese_bbox']['rect'])) for p in body['placements'])
        assert norm(text)==norm('非常に長い日本語文章。'*1000)
    count=len(runtime.inputs);generate(client,sid,[1],retry_failed=True);assert len(runtime.inputs)==count


def test_missing_glyph_retains_original_display_with_reason(client):
    runtime=FaultRuntime('missing_glyph');client.app.state.ai_runtime=runtime
    sid=upload(client,make_japanese_fixture('missing_glyph'));status,r=generate(client,sid,[1])
    assert status['state']=='preview_ready',status
    unit=next(u for u in status['report']['units'] if u['reason']=='missing_glyph')
    with fitz.open(stream=r.content,filetype='pdf') as doc:
        assert 'The synthetic method' in doc[unit['placements'][0]['japanese_page']-1].get_text()


def test_bounded_retry_only_uncertain_units_and_success_cache(client):
    runtime=FaultRuntime('partial'); client.app.state.ai_runtime=runtime
    sid=upload(client,make_japanese_fixture('simple')); status,_=generate(client,sid,[1])
    assert status['state']=='preview_ready'
    assert len(runtime.inputs)==2 and len(runtime.inputs[1]['blocks'])==1
    first=runtime.inputs[0]['blocks']; assert runtime.inputs[1]['blocks'][0]['block_id']==first[-1]['block_id']
    generate(client,sid,[1],retry_failed=True)
    assert len(runtime.inputs)==2


def test_bad_ai_fails_without_original_or_user_data_loss(client):
    sid=upload(client,make_japanese_fixture('simple')); original=client.get(f'/api/sources/{sid}/file').content
    client.app.state.ai_runtime=FaultRuntime('bad')
    status,r=generate(client,sid,[1])
    assert status['state']=='failed' and r.status_code==404
    assert 2 < len(client.app.state.ai_runtime.inputs) <= 48
    assert any(len(turn['blocks'])==1 for turn in client.app.state.ai_runtime.inputs[2:])
    assert all(len(turn['blocks'])<len(client.app.state.ai_runtime.inputs[0]['blocks']) for turn in client.app.state.ai_runtime.inputs[2:])
    assert client.get(f'/api/sources/{sid}/file').content==original
    assert not list(generated_dir().glob('*.pdf'))
    client.app.state.ai_runtime=MockRuntime()
    status,_=generate(client,sid,[1],retry_failed=True)
    assert status['state']=='preview_ready'


def test_same_origin_ownership_and_safe_paths(client):
    sid=upload(client,make_japanese_fixture('simple')); other=upload(client,make_japanese_fixture('two_column'))
    assert client.post(f'/api/sources/{sid}/japanese-pdf/generate',json={'pages':[1]},headers={'Origin':'https://other.test'}).status_code==403
    status,_=generate(client,sid,[1]); token=status['generation_id']
    assert client.get(f'/api/sources/{other}/japanese-pdf/{token}/file').status_code==404
    assert client.get(f'/api/sources/{sid}/japanese-pdf/bad-token/file').status_code==404
    with closing(get_db()) as con:
        con.execute("UPDATE source_versions SET raw_path='../secret.pdf' WHERE source_id=?",(sid,));con.commit()
    assert client.post(f'/api/sources/{sid}/japanese-pdf/generate',json={'pages':[1]}).status_code==409


def test_interrupted_job_recovery_and_duplicate_active_contract(client):
    sid=upload(client,make_japanese_fixture('simple'))
    from app.routes_japanese_pdf import snapshot
    with closing(get_db()) as con:
        _,v,_,_,d=snapshot(con,sid)
        con.execute("INSERT INTO japanese_pdf_generations(id,source_id,source_version_id,source_hash,document_digest,pages_json,state,created_at,updated_at) VALUES('aaaaaaaaaaaaaaaa',?,?,?,?,?,'generating','old','old')",(sid,v['id'],v['content_hash'],d,'[1]')); con.commit()
    assert client.post(f'/api/sources/{sid}/japanese-pdf/generate',json={'pages':[1]}).json()['generation_id']=='aaaaaaaaaaaaaaaa'
    assert client.post(f'/api/sources/{sid}/japanese-pdf/generate',json={'pages':[2]}).status_code==409
    with TestClient(create_app()) as restarted:
        result=restarted.get(f'/api/sources/{sid}/japanese-pdf').json()
        assert result['state']=='failed' and result['error']=='interrupted'


def test_user_translation_reused_and_never_overwritten(client):
    sid=upload(client,make_japanese_fixture('simple'))
    doc=client.get(f'/api/sources/{sid}/document').json()
    block=next(b for b in doc['blocks'] if b['text']=='Synthetic Research Fixture')
    with closing(get_db()) as con:
        con.execute("INSERT INTO translations(id,source_id,version_id,block_id,source_text,translated_text,user_edited,provider,model,created_at,updated_at) VALUES('usertranslation',?,?,?,?,?,1,'user','user','old','old')",
            (sid,doc['version']['id'],block['id'],block['text'],'ユーザー修正済みの資料'))
        con.commit()
    status,r=generate(client,sid,[1])
    assert status['state']=='preview_ready'
    with closing(get_db()) as con:
        rows=con.execute('SELECT * FROM translations WHERE block_id=?',(block['id'],)).fetchall()
        assert len(rows)==1 and rows[0]['translated_text']=='ユーザー修正済みの資料'
        assert rows[0]['user_edited']==1 and rows[0]['created_at']=='old' and rows[0]['provider']=='user'
    with fitz.open(stream=r.content,filetype='pdf') as document:
        assert 'ユーザー修正済みの資料' in document[0].get_text()
        assert any('NOTICE' in n for n in document.embfile_names())
        assert any('Droid' in f[3] for f in document[0].get_fonts())


def test_atomic_publication_failure_keeps_original_and_can_retry(client,monkeypatch):
    import app.routes_japanese_pdf as routes
    sid=upload(client,make_japanese_fixture('simple')); original=client.get(f'/api/sources/{sid}/file').content
    replacement=routes.os.replace
    def fail(*args): raise OSError('synthetic atomic failure')
    monkeypatch.setattr(routes.os,'replace',fail)
    status,r=generate(client,sid,[1])
    assert status['state']=='failed' and r.status_code==404
    assert client.get(f'/api/sources/{sid}/file').content==original
    assert not list(generated_dir().glob('*.pdf'))
    monkeypatch.setattr(routes.os,'replace',replacement)
    assert generate(client,sid,[1],retry_failed=True)[0]['state']=='preview_ready'


def test_text_missing_or_duplicated_cannot_be_published(client,monkeypatch):
    import app.japanese_pdf as engine
    render=engine.Reflow.paragraph
    def duplicate(self,unit):
        render(self,unit)
        unit['placements'].append(unit['placements'][0].copy())
    monkeypatch.setattr(engine.Reflow,'paragraph',duplicate)
    sid=upload(client,make_japanese_fixture('simple'));original=client.get(f'/api/sources/{sid}/file').content
    status,r=generate(client,sid,[1])
    assert status['state']=='failed' and status['error']=='text_missing_or_duplicate'
    assert r.status_code==404 and client.get(f'/api/sources/{sid}/file').content==original
    assert not list(generated_dir().glob('*.pdf'))


def test_legacy_translate_checks_owner_and_ignores_empty_skips(client):
    sid=upload(client,make_japanese_fixture('reader_detail'))
    other=upload(client,make_japanese_fixture('simple'))
    status,_=generate(client,sid,[1])
    doc=client.get(f'/api/sources/{sid}/document').json()
    block=next(b for b in doc['blocks'] if b['text']=='Input')
    assert block['id'] not in doc['translations']
    assert client.post('/api/translate',json={'source_id':other,'block_id':block['id'],'text':'Input'}).status_code==422
    response=client.post('/api/translate',json={'source_id':sid,'block_id':block['id'],'text':'Input'})
    assert response.status_code==200 and response.json()['translation']
    with closing(get_db()) as con:
        rows=con.execute('SELECT * FROM translations WHERE block_id=?',(block['id'],)).fetchall()
        assert len(rows)==1 and rows[0]['source_hash'] and rows[0]['original_bbox']
        assert rows[0]['prompt_version']=='translate-1'


def test_english_legacy_cache_cannot_be_counted_as_japanese(client):
    sid=upload(client,make_japanese_fixture('simple'))
    doc=client.get(f'/api/sources/{sid}/document').json()
    block=next(b for b in doc['blocks'] if b['kind']=='para' and b['page']==1)
    with closing(get_db()) as con:
        con.execute("INSERT INTO translations(id,source_id,version_id,block_id,source_text,translated_text,user_edited,created_at,updated_at) VALUES('englishcache',?,?,?,?,?,1,'old','old')",
                    (sid,doc['version']['id'],block['id'],block['text'],block['text']))
        con.commit()
    status,r=generate(client,sid,[1])
    unit=next(u for u in status['report']['units'] if u['block_id']==block['id'])
    assert unit['status']=='failed' and unit['reason']=='cached_not_japanese'
    with fitz.open(stream=r.content,filetype='pdf') as pdf:
        assert 'This generated document' in pdf[0].get_text()


def test_legacy_fixed_bbox_artifact_retained_but_not_reused_for_reflow(client):
    sid=upload(client,make_japanese_fixture('simple'));first,pdf=generate(client,sid,[1])
    token=first['generation_id']
    with closing(get_db()) as con:
        report=first['report'];report.pop('layout_version')
        con.execute('UPDATE japanese_pdf_generations SET report_json=? WHERE id=?',(json.dumps(report),token));con.commit()
    fresh,other=generate(client,sid,[1])
    assert fresh['generation_id']!=token and fresh['report']['layout_version']=='reflow-3'
    assert client.get(f'/api/sources/{sid}/japanese-pdf/{token}/file').content==pdf.content
    assert len(fresh['artifacts'])==2


def test_ambiguous_caption_preserves_complete_source_page_and_reports_unresolved(client):
    from tests.fixtures.pdf_factory import _text
    with fitz.open() as doc:
        p=doc.new_page(width=595,height=842)
        _text(p,(40,30,555,70),'Abstract',size=18,bold=True)
        for x in (40,300): p.draw_rect((x,150,x+200,300),color=(0,0,0),fill=(.8,.9,1))
        _text(p,(40,315,555,355),'Figure 1. Synthetic input-encoder-output architecture.')
        q=doc.new_page(width=595,height=842)
        _text(q,(40,30,555,70),'1 Introduction',size=18,bold=True)
        _text(q,(40,100,555,200),BODY)
        original=doc.tobytes()
    status,r=generate(client,upload(client,original),[1,2])
    assert status['state']=='preview_ready',status
    asset=next(a for a in status['report']['assets'] if a['source_page']==1)
    assert asset['status']=='unresolved' and asset['reason']=='ambiguous_caption'
    assert asset['source_bbox']==[0,0,595,842] and not asset['caption_block_ids']
    assert any(q['asset_id']==asset['id'] and q['source_text_preserved'] for q in status['report']['quality']['visuals'])


def test_layout_overlap_cannot_be_published(client,monkeypatch):
    import app.japanese_pdf as engine
    paragraph=engine.Reflow.paragraph
    def overlap(self,unit):
        paragraph(self,unit)
        self.flow.append(dict(self.flow[-1]))
    monkeypatch.setattr(engine.Reflow,'paragraph',overlap)
    status,pdf=generate(client,upload(client,make_japanese_fixture('simple')),[1])
    assert status['state']=='failed' and status['error']=='layout_overlap' and pdf.status_code==404


def test_visible_annotation_appearance_is_preserved(client):
    from tests.fixtures.pdf_factory import _text
    with fitz.open() as doc:
        p=doc.new_page(width=595,height=842)
        _text(p,(40,40,555,90),'Abstract',size=18,bold=True)
        a=p.add_rect_annot((40,150,400,300));a.set_colors(stroke=(0,0,0),fill=(1,1,0));a.update()
        q=doc.new_page(width=595,height=842)
        _text(q,(40,40,555,90),'1 Introduction',size=18,bold=True)
        _text(q,(40,150,555,300),BODY)
        original=doc.tobytes()
    status,_=generate(client,upload(client,original),[1,2])
    assert status['state']=='preview_ready',status
    asset=next(a for a in status['report']['assets'] if a['source_page']==1)
    assert asset['rasterize_annotations'] and not asset['vector_preserved']
    assert asset['reason']=='annotations_present'


def test_multiline_caption_continuation_stays_with_visual(client):
    from tests.fixtures.pdf_factory import _text
    with fitz.open() as doc:
        p=doc.new_page(width=595,height=842)
        _text(p,(40,30,555,70),'Abstract',size=18,bold=True)
        _text(p,(40,90,555,130),BODY)
        p.draw_rect((40,200,550,300),color=(0,0,0))
        _text(p,(40,315,555,345),'Figure 1. Synthetic input-encoder-output architecture')
        _text(p,(40,331,555,365),'with a frozen backbone in a few-shot setting.')
        data=doc.tobytes()
    # These synthetic caption strings aren't in the deterministic mock dictionary:
    # retain their English text, keep both blocks beside the same original visual.
    status,r=generate(client,upload(client,data),[1])
    assert status['state']=='preview_ready',status
    asset=next(a for a in status['report']['assets'] if a['caption_block_ids'])
    assert len(asset['caption_block_ids'])==2
    assert asset['caption_status']=='untranslated'
    with fitz.open(stream=r.content,filetype='pdf') as pdf:
        page=pdf[asset['japanese_page']-1].get_text()
        assert 'Figure 1.' in page and 'with a frozen backbone' in page


def test_visual_crop_drops_hidden_text_even_in_single_paint_operation():
    from app.japanese_pdf import cropped_visual
    with fitz.open() as original:
        page=original.new_page()
        page.insert_text((40,60),['Outside before','y = x + b (1)','Outside after'],fontsize=11,lineheight=2)
        with cropped_visual(page,fitz.Rect(38,68,120,88)) as crop:
            assert crop[0].get_text(clip=fitz.INFINITE_RECT()).strip()=='y = x + b (1)'
            assert 'Outside before' in original[0].get_text() and 'Outside after' in original[0].get_text()
