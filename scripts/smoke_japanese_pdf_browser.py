"""Reflow UI acceptance with synthetic papers, deterministic translations and images."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from urllib.parse import quote
import fitz
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright, expect
from smoke_import_brief_browser import mock_server
from tests.fixtures.japanese_pdf_factory import make_japanese_fixture


def render(pdf,output,prefix):
    with fitz.open(stream=pdf,filetype='pdf') as doc:
        images=[]
        for n,page in enumerate(doc,1):
            pix=page.get_pixmap(dpi=200,alpha=False)
            pix.save(str(output/f'{prefix}-page-{n}.png'))
            images.append(Image.frombytes('RGB',(pix.width,pix.height),pix.samples))
        return images


def run(base,chromium,output):
    cases=[];errors=[]
    original=make_japanese_fixture('reflow_demo')
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        context=browser.new_context(viewport={'width':1440,'height':1100},device_scale_factor=2)
        context.set_default_timeout(20000)
        page=context.new_page();page.on('pageerror',lambda e:errors.append(getattr(e,'stack',str(e))))
        request=context.request
        def upload(data):
            r=request.post(base+'/api/sources/pdf',multipart={'file':{'name':'generated.pdf','mimeType':'application/pdf','buffer':data},'force':'true'})
            assert r.ok,r.text();return r.json()['source']['id']
        def ready(): page.wait_for_selector('.pdf-page--ready .textLayer span')
        sid=upload(original)
        doc=request.get(base+f'/api/sources/{sid}/document').json()
        page.goto(base+'/#/read/'+sid);ready()
        controls=page.locator('.japanese-pdf-controls')
        original_button=controls.get_by_role('button',name='原文PDF',exact=True)
        japanese=controls.get_by_role('button',name='日本語PDF（試作）',exact=True)
        controls.get_by_text('日本語版を試作',exact=True).click()
        pages=controls.get_by_role('textbox',name='翻訳対象の原文ページ')
        pages.fill('1,2,3,4');controls.get_by_role('button',name='日本語PDFを生成',exact=True).click()
        expect(page.locator('.toast').last).to_contain_text('1〜3ページ')
        pages.fill('1,2,3');controls.get_by_role('button',name='日本語PDFを生成',exact=True).click()
        expect(controls.locator('.japanese-pdf-status')).to_contain_text('日本語再組版',timeout=30000)
        status=request.get(base+f'/api/sources/{sid}/japanese-pdf').json()
        report=status['report'];assert report['layout_version']=='reflow-5'
        cases+=['original page subset validation','UI reflow generation and persisted artifact']
        page.get_by_role('spinbutton',name='PDF倍率（%）',exact=True).fill('125')
        page.get_by_role('spinbutton',name='PDF倍率（%）',exact=True).press('Enter')
        page.get_by_role('button',name='PDFを全幅で読む',exact=True).click()
        japanese.click();ready()
        expect(japanese).to_have_attribute('aria-pressed','true')
        expect(page.locator('.reader-pane-right')).to_be_hidden()
        assert page.get_by_role('spinbutton',name='PDF倍率（%）',exact=True).input_value()=='125'
        assert page.locator('.pdf-scroll').count()==1
        page.screenshot(path=str(output/'browser-japanese-reflow.png'))
        assert page.evaluate("async()=> (await (await import('/js/components/pdfviewer.js')).findTextMatches('Synthetic column content for reading order.')).length") == 0
        assert page.evaluate("async()=> (await (await import('/js/components/pdfviewer.js')).findTextMatches('Synthetic benchmark scores (invented test values).')).length") == 0
        cases+=['single viewer reflow PDF','zoom and panel state preserved']
        # Japanese search uses actual PDF text, including pages not yet rendered.
        search=page.get_by_placeholder('文書内検索');search.fill('左列の段落6')
        page.wait_for_timeout(350);search.press('Enter')
        expect(page.locator('.doc-toolbar__search-count')).to_contain_text('1/1')
        page.wait_for_selector('.search-hit-active')
        cases.append('Japanese full PDF search')
        span=page.locator('.textLayer span').filter(has_text='左列の段落6').first
        span.scroll_into_view_if_needed()
        span.evaluate("""s=>{const r=document.createRange();r.selectNodeContents(s);const sel=getSelection();sel.removeAllRanges();sel.addRange(r);s.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));}""")
        expect(page.locator('.selection-popover')).to_contain_text('原文を見る')
        assert page.locator('.selection-popover button').count()==1
        page.locator('.selection-popover button').click()
        expect(original_button).to_have_attribute('aria-pressed','true')
        marker=page.locator('.pdf-evidence-marker');expect(marker).to_be_visible()
        assert marker.locator('..').get_attribute('data-pdf-page')=='2'
        source_block=next(b for b in doc['blocks'] if b['text'].startswith('LEFT paragraph 6.'))
        saved=request.get(base+f'/api/sources/{sid}/document').json();assert not saved['highlights']
        cases+=['Japanese selection returns original block on source page 2','no Japanese character bbox saved as SourceAnchor']
        # A single source page 2 reflows into Japanese page 1; identity jumps must
        # work even when the two page numbers are demonstrably different.
        pages.fill('2');controls.get_by_role('button',name='日本語PDFを生成',exact=True).click()
        expect(controls.locator('.japanese-pdf-status')).to_contain_text('原文 p.2 →',timeout=30000)
        subset=request.get(base+f'/api/sources/{sid}/japanese-pdf').json()
        source_anchor={'blockId':source_block['id'],'sourceVersion':doc['version']['id']}
        page.evaluate("async a=>(await import('/js/views/reader.js')).resolveAnchor(a)",source_anchor)
        japanese.click();ready()
        placement=next(u for u in subset['report']['units'] if u['block_id']==source_block['id'])['placements'][0]
        assert placement['japanese_page']==1 and source_block['page']==2
        assert page.get_by_role('spinbutton',name='PDFページ番号',exact=True).input_value()=='1'
        cases.append('block mapping source p.2 -> Japanese p.1, not page-number synchronization')
        # Explicit Evidence always returns to authoritative original, even outside
        # the subset and after saving the Japanese display preference.
        table_block=next(b for b in doc['blocks'] if b['page']==3 and 'Synthetic-06' in b['text'])
        anchor={'blockId':table_block['id'],'sourceVersion':doc['version']['id'],'page':3,'bbox':table_block['bbox']}
        evidence=quote(json.dumps({'anchor':anchor,'kind':'resolved'}))
        page.goto(base+'/#/read/'+sid+'?evidence='+evidence)
        expect(original_button).to_have_attribute('aria-pressed','true')
        expect(page.locator('.pdf-evidence-marker')).to_be_visible()
        assert page.locator('.pdf-evidence-marker').locator('..').get_attribute('data-pdf-page')=='3'
        page.evaluate("history.replaceState(null,'',location.href.split('?')[0])")
        cases.append('Evidence URL overrides Japanese preference and subset page limits')
        controls.get_by_role('combobox',name='生成済み日本語PDF').select_option(status['generation_id'])
        japanese.click();ready()
        search.fill('66.012345');page.wait_for_timeout(350);search.press('Enter')
        expect(page.locator('.doc-toolbar__search-count')).to_contain_text('1/1')
        page.wait_for_selector('.search-hit-active')
        page.screenshot(path=str(output/'browser-japanese-table.png'))
        cases.append('original decimal table text searchable after vector placement')
        controls.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        expect(controls.locator('.japanese-pdf-report')).to_contain_text('Caption対応済み')
        expect(controls.locator('.japanese-pdf-report')).to_contain_text('原文 p.3 → 日本語 p.')
        page.screenshot(path=str(output/'browser-source-alignment.png'))
        controls.get_by_text('原文との対応・原物保持・未解決の理由',exact=True).click()
        with page.expect_download() as downloaded: controls.get_by_role('link',name='日本語PDFを保存',exact=True).click()
        downloaded.value.save_as(output/'japanese-prototype.pdf')
        pdf=(output/'japanese-prototype.pdf').read_bytes()
        assert pdf[:5]==b'%PDF-'
        cases+=['visual/caption and original/Japanese locations visible','download reflow PDF']
        current_page=page.get_by_role('spinbutton',name='PDFページ番号',exact=True).input_value()
        page.reload();expect(japanese).to_have_attribute('aria-pressed','true');ready()
        assert page.get_by_role('spinbutton',name='PDFページ番号',exact=True).input_value()==current_page
        assert page.get_by_role('spinbutton',name='PDF倍率（%）',exact=True).input_value()=='125'
        expect(page.locator('.reader-pane-right')).to_be_hidden()
        cases.append('reload restores artifact-specific Japanese page, zoom, panel')
        page.get_by_role('button',name='幅に合わせる',exact=True).click()
        original_button.click();japanese.click();ready()
        expect(page.get_by_role('button',name='幅に合わせる',exact=True)).to_have_attribute('aria-pressed','true')
        cases.append('fit mode retained through semantic switch')
        page.get_by_role('button',name='テキスト表示',exact=True).click();page.reload()
        expect(page.locator('.doc-scroll')).to_be_visible()
        page.get_by_role('button',name='PDF表示',exact=True).click();expect(japanese).to_have_attribute('aria-pressed','true')
        cases.append('text/PDF mode separate from Japanese/original PDF')
        # Long translation flows to multiple pages at 11pt rather than overflowing.
        long_data=make_japanese_fixture('long_reflow');long_sid=upload(long_data)
        page.goto(base+'/#/read/'+long_sid);ready()
        c=page.locator('.japanese-pdf-controls');c.get_by_text('日本語版を試作',exact=True).click()
        c.get_by_role('textbox',name='翻訳対象の原文ページ').fill('1')
        c.get_by_role('button',name='日本語PDFを生成',exact=True).click()
        expect(c.locator('.japanese-pdf-status')).to_contain_text('日本語再組版',timeout=30000)
        long_status=request.get(base+f'/api/sources/{long_sid}/japanese-pdf').json()
        assert long_status['report']['japanese_page_count']>1
        assert not long_status['report']['counts'].get('overflow')
        c.get_by_role('button',name='日本語PDF（試作）',exact=True).click();ready()
        page.screenshot(path=str(output/'browser-japanese-long-text.png'))
        long_pdf=request.get(base+f'/api/sources/{long_sid}/japanese-pdf/{long_status["generation_id"]}/file').body()
        (output/'japanese-long.pdf').write_bytes(long_pdf)
        cases.append('long Japanese text naturally paginates without shrinking/overflow')
        # Exercise cancellation while documents are loading and rendering.
        page.evaluate("""async()=>{const b=document.querySelector('.japanese-pdf-actions').querySelectorAll('button');
          for(let i=0;i<20;i++){b[0].click();b[1].click();await new Promise(r=>setTimeout(r,10));}}""")
        page.goto(base+'/#/');assert not errors,errors
        cases.append('rapid document switching and route teardown without errors')
        browser.close()
    english=render(original,output,'english');japanese_images=render(pdf,output,'japanese')
    render(long_pdf,output,'japanese-long')
    # These pairs illustrate changed pagination; labels use source/output page
    # numbers separately and deliberately do not imply one-to-one correspondence.
    for n in range(max(len(english),len(japanese_images))):
        a=english[min(n,len(english)-1)].copy();b=japanese_images[n].copy()
        a.thumbnail((800,1200));b.thumbnail((800,1200))
        pair=Image.new('RGB',(a.width+b.width,max(a.height,b.height)+28),'white')
        pair.paste(a,(0,28));pair.paste(b,(a.width,28))
        draw=ImageDraw.Draw(pair);draw.text((12,6),f'Source p.{min(n,len(english)-1)+1}',fill='black');draw.text((a.width+12,6),f'Japanese p.{n+1}',fill='black')
        pair.save(output/f'comparison-page-{n+1}.png')
    final={'passed':len(cases),'cases':cases,'pageerrors':errors,'original_hash':hashlib.sha256(original).hexdigest(),
           'pdf_hash':hashlib.sha256(pdf).hexdigest(),'generation_report':report,'long_report':long_status['report']}
    (output/'browser-report.json').write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':len(cases),'cases':cases,'pageerrors':errors,'japanese_pages':report['japanese_page_count'],'counts':report['counts']},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--chromium',default=shutil.which('chromium'))
    parser.add_argument('--output',type=Path,default=Path(tempfile.gettempdir())/'kg-japanese-pdf-smoke')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with mock_server() as base:run(base,args.chromium,args.output)
