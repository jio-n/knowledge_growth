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
from app.japanese_pdf import compare_pixels
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


@pytest.mark.parametrize('kind,pages', [('simple',[1]), ('two_column',[1]), ('reader_detail',[1,2]), ('layout',[1,2,3])])
def test_layout_protection_extraction_and_original_hash(client, kind, pages):
    original = make_japanese_fixture(kind)
    sid = upload(client, original)
    status, response = generate(client, sid, pages)
    assert status['state'] == 'preview_ready', status
    assert response.status_code == 200
    report = status['report']
    assert report['counts']['translated'] > 0
    assert all(q['outside_pixels'] == 0 and q['dpi'] == 200 for q in report['quality'])
    with closing(get_db()) as con:
        src = con.execute('SELECT * FROM sources WHERE id=?', (sid,)).fetchone()
        assert hashlib.sha256((files_dir()/src['file_path']).read_bytes()).hexdigest() == hashlib.sha256(original).hexdigest()
        units = con.execute('SELECT * FROM translations WHERE source_id=?', (sid,)).fetchall()
        assert all(u['version_id'] and u['source_hash'] == src['content_hash'] and u['original_bbox'] and u['created_at'] and u['updated_at'] for u in units)
        assert all(u['runtime'] == 'mock' and u['prompt_version'] == 'japanese-pdf-0.1' for u in units if u['translated_text'])
    with fitz.open(stream=original, filetype='pdf') as before, fitz.open(stream=response.content, filetype='pdf') as after:
        assert len(before) == len(after)
        for number,(a,b) in enumerate(zip(before,after),1):
            assert (a.mediabox,a.cropbox,a.rotation) == (b.mediabox,b.cropbox,b.rotation)
            changed = [u['bbox']['rect'] for u in report['units'] if u['page'] == number and u['status']=='translated']
            assert compare_pixels(a,b,changed)['outside_pixels'] == 0
            for unit in report['units']:
                if unit['page'] != number: continue
                rect = fitz.Rect(unit['bbox']['rect'])
                if unit['status'] == 'translated':
                    assert any('\u3040' <= ch <= '\u9fff' for ch in b.get_text('text',clip=rect))
                    assert '□' not in b.get_text() and '�' not in b.get_text()
                    assert a.get_text('text',clip=rect).strip() not in b.get_text('text',clip=rect)
                else:
                    assert a.get_text('text',clip=rect) == b.get_text('text',clip=rect)
            if number not in pages: assert compare_pixels(a,b,[])['changed_pixels'] == 0
        if kind=='reader_detail':
            # Exact pixels in graph, table, equation and model/numeric text regions.
            for page, rect in [(0,(42,175,540,265)),(0,(95,465,525,515)),(1,(35,140,560,566))]:
                assert before[page].get_pixmap(clip=rect,dpi=200).samples == after[page].get_pixmap(clip=rect,dpi=200).samples
            assert '66.012345' in after[1].get_text()


@pytest.mark.parametrize('rotation', [90,180,270])
def test_rotated_cropbox_preserves_selected_and_unselected(client, rotation):
    data = make_japanese_fixture('simple',rotation=rotation,crop=True)
    status,r=generate(client,upload(client,data),[1])
    assert status['state']=='preview_ready',status
    with fitz.open(stream=data,filetype='pdf') as a,fitz.open(stream=r.content,filetype='pdf') as b:
        assert a[0].cropbox == b[0].cropbox and b[0].rotation == rotation
        assert compare_pixels(a[1],b[1],[])['changed_pixels']==0


@pytest.mark.parametrize('kind', ['near','overlap','symbol','unruled'])
def test_ambiguous_regions_remain_original(client,kind):
    data=make_japanese_fixture(kind)
    status,r=generate(client,upload(client,data),[1])
    assert status['state']=='preview_ready',status
    assert any(u['status'].startswith('skipped_') for u in status['report']['units'])
    with fitz.open(stream=data,filetype='pdf') as a,fitz.open(stream=r.content,filetype='pdf') as b:
        rect=(30,440,550,550) if kind in ('symbol','unruled') else (30,290,550,550)
        assert a[0].get_pixmap(clip=rect,dpi=200).samples==b[0].get_pixmap(clip=rect,dpi=200).samples


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
        if not self.sessions[session.id].startswith('# japanese-pdf-0.1'):
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


@pytest.mark.parametrize('mode,status_reason',[('overflow','minimum_readable_size'),('missing_glyph','missing_glyph')])
def test_unplaceable_translation_retains_original_and_cached_text(client,mode,status_reason):
    runtime=FaultRuntime(mode); client.app.state.ai_runtime=runtime
    sid=upload(client,make_japanese_fixture(mode)); status,r=generate(client,sid,[1])
    assert status['state']=='preview_ready',status
    failed=next(u for u in status['report']['units'] if u['reason']==status_reason)
    with fitz.open(stream=r.content,filetype='pdf') as doc:
        assert 'The synthetic method' in doc[0].get_text('text',clip=fitz.Rect(failed['bbox']['rect']))
    count=len(runtime.inputs)
    generate(client,sid,[1],retry_failed=True)
    assert len(runtime.inputs)==count  # layout overflow doesn't repeat an AI turn


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
    assert len(client.app.state.ai_runtime.inputs)==2
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


def test_pixels_outside_allowed_boxes_reject_whole_pdf(client,monkeypatch):
    import app.japanese_pdf as engine
    original_place=engine.place_block
    def unsafe(document,*args):
        original_place(document,*args)
        document[0].draw_line((20,600),(400,600),width=2)
    monkeypatch.setattr(engine,'place_block',unsafe)
    sid=upload(client,make_japanese_fixture('simple')); original=client.get(f'/api/sources/{sid}/file').content
    status,r=generate(client,sid,[1])
    assert status['state']=='failed' and status['error']=='changes_outside_text_bbox'
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
