"""Optional Playwright smoke check using generated PDF fixtures only."""
import json
import sys
import argparse
import shutil
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from tests.fixtures.pdf_factory import make_pdf

parser = argparse.ArgumentParser(description="Phase 1 browser smoke; use a disposable mock app data directory.")
parser.add_argument('--base-url', default='http://127.0.0.1:8301')
parser.add_argument('--chromium', default=shutil.which('chromium'))
parser.add_argument('--output-dir', type=Path, default=Path(tempfile.gettempdir()) / 'kg-phase1-browser')
args = parser.parse_args()
args.output_dir.mkdir(parents=True, exist_ok=True)
BASE = args.base_url.rstrip('/')
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=args.chromium, headless=True, args=['--no-sandbox'])
    context=browser.new_context(viewport={'width':1440,'height':1000})
    page=context.new_page()
    errors=[]
    context.set_default_timeout(10000)
    page.on('pageerror', lambda error: errors.append(str(error)))
    def upload(name, **kwargs):
        response=context.request.post(BASE+'/api/sources/pdf', multipart={
            'file':{'name':name+'.pdf','mimeType':'application/pdf','buffer':make_pdf(name, **kwargs)}, 'force':'true'})
        assert response.ok, response.text()
        sid=response.json()['source']['id']
        doc=context.request.get(BASE+f'/api/sources/{sid}/document').json()
        page.goto(BASE+f'/#/read/{sid}')
        page.wait_for_function("document.querySelectorAll('.textLayer span').length > 5 && document.querySelectorAll('.pdf-page canvas').length === 2")
        return sid,doc
    def resolve(anchor):
        return page.evaluate("""async a => {
          const m=await import('/js/views/reader.js'); const r=m.resolveAnchor(a);
          return {status:r.status, method:r.method, id:r.block?.id, candidates:r.candidates.length};
        }""", anchor)
    sid,doc=upload('two_column')
    assert len(page.locator('.pdf-page canvas').all())==2
    right=next(b for b in doc['blocks'] if b['text'].startswith('RIGHT paragraph 6.'))
    span=page.locator('.textLayer span').filter(has_text='RIGHT paragraph 6.').first
    span.scroll_into_view_if_needed()
    span.evaluate("""s=>{
      const range=document.createRange(); range.selectNodeContents(s);
      const sel=window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
      s.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
    }""")
    page.locator('.selection-popover').get_by_role('button',name='ハイライト',exact=True).click()
    page.wait_for_function('!document.querySelector(".selection-popover")')
    current=context.request.get(BASE+f'/api/sources/{sid}/document').json()
    assert len(current['highlights'])==1
    anchor=json.loads(current['highlights'][0]['anchor'])
    assert anchor['sourceVersion']==doc['version']['id'] and anchor['sourceHash']==doc['version']['content_hash']
    assert anchor['blockId']==right['id'], anchor
    assert anchor['bbox']['rect'][0]>300
    assert resolve(anchor)['method']=='block_id'
    marker=page.locator('.pdf-evidence-marker')
    marker.wait_for()
    assert float(marker.evaluate('m=>parseFloat(m.style.left)'))>300
    page.get_by_role('button',name='テキスト表示',exact=True).click()
    page.wait_for_selector('.kg-highlight')
    assert 'RIGHT paragraph 6.' in page.locator('.kg-highlight').inner_text()
    page.reload()
    page.wait_for_selector('.kg-highlight')
    assert 'RIGHT paragraph 6.' in page.locator('.kg-highlight').inner_text()
    # Original block selection creates provenance with bbox and invokes the
    # existing selection -> mock Q&A -> persisted SourceAnchor workflow.
    body=page.locator(f'.block-body[data-block-id="{right["id"]}"]')
    body.scroll_into_view_if_needed()
    body.evaluate("""s=>{
      const range=document.createRange();range.selectNodeContents(s);
      const sel=window.getSelection();sel.removeAllRanges();sel.addRange(range);
      s.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
    }""")
    page.locator('.selection-popover').get_by_role('button',name='説明',exact=True).click()
    page.wait_for_selector('.qa-answer')
    questions=context.request.get(BASE+f'/api/sources/{sid}/questions').json()['questions']
    assert questions[0]['anchor']['sourceVersion']==doc['version']['id']
    assert questions[0]['anchor']['bbox']==right['bbox']
    page.get_by_role('button',name='PDF表示',exact=True).click()
    assert resolve({**anchor, 'sourceVersion':'old', 'blockId':'stale', 'bbox':None})['method']=='quote'
    assert resolve({**anchor, 'blockId':'missing', 'quote':None})['method']=='bbox'
    page.screenshot(path=str(args.output_dir / 'bbox.png'))
    # Equal quote matches display choices instead of jumping to the first one.
    sid,doc=upload('duplicate_evidence')
    result=resolve({'quote':'Shared evidence phrase.','page':1,'blockIdx':2})
    assert result['status']=='candidates' and result['candidates']==2
    assert page.locator('.anchor-candidates button').count()==2
    assert page.locator('.pdf-evidence-marker').count()==0
    page.screenshot(path=str(args.output_dir / 'candidates.png'))
    assert resolve({'page':2,'quote':'missing evidence'})['status']=='page_only'
    # Rotated and cropped pages round-trip selection coordinates and display bbox.
    sid,doc=upload('simple',rotation=90,crop=True)
    block=next(b for b in doc['blocks'] if b['kind']=='para' and b['page']==1)
    span=page.locator('.textLayer span').filter(has_text='This generated document tests').first
    span.scroll_into_view_if_needed()
    span.evaluate("""s=>{
      const range=document.createRange(); range.selectNodeContents(s);
      const sel=window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
      s.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
    }""")
    page.locator('.selection-popover').get_by_role('button',name='ハイライト',exact=True).click()
    current=context.request.get(BASE+f'/api/sources/{sid}/document').json()
    anchor=json.loads(current['highlights'][0]['anchor'])
    assert anchor['blockId']==block['id'] and anchor['bbox']['page_rect']==[0,0,555,802]
    assert resolve(anchor)['status']=='resolved'
    marker=page.locator('.pdf-evidence-marker');marker.wait_for()
    box=marker.bounding_box()
    assert box['width']>10 and box['height']>10
    page.screenshot(path=str(args.output_dir / 'rotated.png'))
    assert not errors,errors
    print(json.dumps({'browser':'Chromium', 'canvas_pages':2,'pdf_selection':'right column + rotated crop',
                      'anchor_provenance':'version/hash/bbox persisted', 'highlight_reload':'passed',
                      'mock_qa':'passed','fallbacks':['quote','bbox','candidates','page_only'], 'pageerrors':errors}))
    browser.close()
