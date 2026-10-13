"""Windows-facing UI acceptance with synthetic PDFs and deterministic AIRuntime.

Exports browser screenshots, English/Japanese 200dpi renders, a PDF and report.
Requires optional Playwright/Chromium. No external paper or ChatGPT login.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from urllib.parse import quote
import fitz
from PIL import Image
from smoke_import_brief_browser import mock_server
from smoke_reader_browser import POSITION
from playwright.sync_api import sync_playwright, expect
from tests.fixtures.japanese_pdf_factory import make_japanese_fixture


def run(base, chromium, output):
    cases, errors = [], []
    original = make_japanese_fixture()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium, headless=True, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width':1440,'height':1100},device_scale_factor=2)
        context.set_default_timeout(20000)
        page = context.new_page(); page.on('pageerror',lambda e:errors.append(getattr(e,'stack',str(e))))
        r=context.request.post(base+'/api/sources/pdf',multipart={'file':{'name':'generated.pdf','mimeType':'application/pdf','buffer':original}})
        assert r.ok,r.text
        sid=r.json()['source']['id']
        doc=context.request.get(base+f'/api/sources/{sid}/document').json()
        page.goto(base+'/#/read/'+sid)
        page.wait_for_selector('.pdf-page--ready .textLayer span')
        controls=page.locator('.japanese-pdf-controls')
        expect(controls).to_contain_text('未生成')
        controls.get_by_text('日本語版を試作',exact=True).click()
        input=controls.get_by_role('textbox',name='翻訳対象ページ')
        input.fill('1,2,3,4'); controls.get_by_role('button',name='日本語PDFを生成',exact=True).click()
        expect(page.locator('.toast').last).to_contain_text('1〜3ページ')
        input.fill('1,2,3'); controls.get_by_role('button',name='日本語PDFを生成',exact=True).click()
        expect(controls.locator('.japanese-pdf-status')).to_contain_text('日本語試作・一部ページのみ翻訳済み',timeout=30000)
        status=context.request.get(base+f'/api/sources/{sid}/japanese-pdf').json()
        assert status['state']=='preview_ready',status
        cases+=['UI maximum-three-page validation','UI generation -> persisted preview']
        japanese=controls.get_by_role('button',name='日本語PDF（試作）',exact=True)
        original_button=controls.get_by_role('button',name='原文PDF',exact=True)
        # Set a viewport center sufficiently far from scroll boundaries for a strict test.
        page.get_by_role('spinbutton',name='PDFページ番号',exact=True).fill('2')
        page.get_by_role('spinbutton',name='PDFページ番号',exact=True).press('Enter')
        page.get_by_role('spinbutton',name='PDF倍率（%）',exact=True).fill('125')
        page.get_by_role('spinbutton',name='PDF倍率（%）',exact=True).press('Enter')
        page.get_by_role('button',name='PDFを全幅で読む',exact=True).click()
        page.evaluate("""()=>{const c=document.querySelector('.pdf-scroll'),p=c.querySelector('[data-pdf-page="2"]');c.scrollTop=p.offsetTop+p.offsetHeight*.45-c.clientHeight/2;}""")
        page.wait_for_timeout(300)
        before=page.evaluate(POSITION)
        japanese.click()
        page.wait_for_function("document.querySelector('.pdf-page[data-pdf-page=\"2\"].pdf-page--ready .textLayer')?.textContent.includes('合成手法')")
        after=page.evaluate(POSITION)
        assert before['page']==after['page']==2 and abs(before['y']-after['y'])<.015,(before,after)
        assert page.get_by_role('spinbutton',name='PDF倍率（%）',exact=True).input_value()=='125'
        expect(page.locator('.reader-pane-right')).to_be_hidden()
        cases+=['original/Japanese relative position preserved','zoom and collapsed side panel preserved','single PDF.js controller']
        assert page.locator('.pdf-scroll').count()==1
        page.screenshot(path=str(output/'browser-japanese-columns.png'))
        original_button.click()
        page.wait_for_function("document.querySelector('.pdf-page[data-pdf-page=\"2\"].pdf-page--ready .textLayer')?.textContent.includes('synthetic method')")
        back=page.evaluate(POSITION)
        assert back['page']==2 and abs(back['y']-before['y'])<.015
        page.screenshot(path=str(output/'browser-original-columns.png'))
        japanese.click()
        page.wait_for_function("document.querySelector('.pdf-page--ready .textLayer')?.textContent.includes('合成手法')")
        # Text is selectable, but the selection offers only a return to original;
        # derived character bboxes cannot become original Highlight anchors.
        span=page.locator('.pdf-page[data-pdf-page="2"] .textLayer span').filter(has_text='合成手法').first
        span.scroll_into_view_if_needed()
        span.evaluate("""s=>{const r=document.createRange();r.selectNodeContents(s);const sel=getSelection();sel.removeAllRanges();sel.addRange(r);s.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));}""")
        expect(page.locator('.selection-popover')).to_contain_text('原文を見る')
        assert page.locator('.selection-popover button').count()==1
        page.locator('.selection-popover').get_by_role('button',name='原文を見る',exact=True).click()
        expect(original_button).to_have_attribute('aria-pressed','true')
        page.wait_for_selector('.pdf-page--ready .textLayer span')
        assert not context.request.get(base+f'/api/sources/{sid}/document').json()['highlights']
        cases+=['Japanese TextLayer selection','selection return uses original identity, no derived Highlight bbox']
        # Evidence path always mounts original before resolving existing source bbox.
        japanese.click(); expect(japanese).to_have_attribute('aria-pressed','true')
        block=next(b for b in doc['blocks'] if b['page']==3 and 'Synthetic-06' in b['text'])
        anchor={'blockId':block['id'],'sourceVersion':doc['version']['id'],'page':3,'bbox':block['bbox']}
        page.evaluate("async a=>(await import('/js/views/reader.js')).resolveAnchor(a)",anchor)
        expect(original_button).to_have_attribute('aria-pressed','true')
        expect(page.locator('.pdf-evidence-marker')).to_be_visible()
        expect(page.locator('.pdf-page[data-pdf-page="3"] .textLayer')).to_contain_text('66.012345')
        cases.append('Evidence/SourceAnchor switches to authoritative original page/bbox')
        # Protected table numbers remain readable in the Japanese PDF.
        japanese.click(); expect(japanese).to_have_attribute('aria-pressed','true')
        page.wait_for_selector('.pdf-page[data-pdf-page="3"].pdf-page--ready .textLayer span')
        expect(page.locator('.pdf-page[data-pdf-page="3"] .textLayer')).to_contain_text('66.012345')
        page.screenshot(path=str(output/'browser-japanese-table.png'))
        cases.append('original model/decimal table text retained in Japanese viewer')
        # Saved Japanese preference must not override an explicit Evidence URL.
        evidence=quote(json.dumps({'anchor':anchor,'kind':'resolved'}))
        page.goto(base+'/#/read/'+sid+'?evidence='+evidence)
        expect(page.locator('.pdf-evidence-marker')).to_be_visible()
        expect(original_button).to_have_attribute('aria-pressed','true')
        expect(japanese).to_be_enabled()
        expect(original_button).to_have_attribute('aria-pressed','true')
        page.evaluate("history.replaceState(null,'',location.href.split('?')[0])")
        japanese.click(); expect(japanese).to_have_attribute('aria-pressed','true')
        page.wait_for_selector('.pdf-page[data-pdf-page="3"].pdf-page--ready .textLayer span')
        cases.append('saved Japanese preference cannot override explicit Evidence URL')
        controls.locator('details').last.locator('summary').click()
        expect(controls.locator('.japanese-pdf-report')).to_contain_text('skipped_protected')
        page.screenshot(path=str(output/'browser-skip-report.png'))
        controls.locator('details').last.locator('summary').click()
        with page.expect_download() as downloaded:
            controls.get_by_role('link',name='日本語PDFを保存',exact=True).click()
        downloaded.value.save_as(output/'japanese-prototype.pdf')
        pdf=(output/'japanese-prototype.pdf').read_bytes()
        assert pdf[:5]==b'%PDF-'
        cases+=['skip locations and reasons visible','download PDF']
        page.reload()
        expect(page.locator('.japanese-pdf-controls').get_by_role('button',name='日本語PDF（試作）',exact=True)).to_have_attribute('aria-pressed','true')
        page.wait_for_selector('.pdf-page--ready .textLayer span')
        expect(page.locator('.reader-pane-right')).to_be_hidden()
        assert page.get_by_role('spinbutton',name='PDF倍率（%）',exact=True).input_value()=='125'
        cases.append('reload restores generated artifact, zoom and panel state')
        # Fit mode also survives replacing the sole viewer.
        page.get_by_role('button',name='幅に合わせる',exact=True).click()
        original_button.click(); japanese.click()
        expect(page.get_by_role('button',name='幅に合わせる',exact=True)).to_have_attribute('aria-pressed','true')
        cases.append('fit-width mode preserved across language switch')
        page.get_by_role('button',name='テキスト表示',exact=True).click()
        page.reload()
        expect(page.locator('.doc-scroll')).to_be_visible()
        page.get_by_role('button',name='PDF表示',exact=True).click()
        expect(japanese).to_have_attribute('aria-pressed','true')
        cases.append('text/PDF display mode remains separate from PDF language')
        # Regeneration is deduplicated, and disjoint pages create another retained artifact.
        controls=page.locator('.japanese-pdf-controls')
        controls.get_by_text('日本語版を試作',exact=True).click()
        controls.get_by_role('textbox',name='翻訳対象ページ').fill('1,3')
        controls.get_by_role('button',name='日本語PDFを生成',exact=True).click()
        expect(controls.locator('.japanese-pdf-status')).to_contain_text('対象 p.1, 3',timeout=30000)
        controls.get_by_role('combobox',name='生成済み日本語PDF').select_option(status['generation_id'])
        expect(controls.locator('.japanese-pdf-status')).to_contain_text('対象 p.1, 2, 3')
        cases.append('noncontiguous page generation and saved artifact selection')
        # Race/teardown: rapid language swaps followed by another route.
        page.evaluate("""async()=>{
          const buttons=document.querySelector('.japanese-pdf-actions').querySelectorAll('button');
          for(let i=0;i<20;i++) { buttons[0].click();buttons[1].click();await new Promise(r=>setTimeout(r,10)); }
        }""")
        page.goto(base+'/#/')
        assert not errors,errors
        cases.append('rapid language switch and route teardown without pageerrors')
        # A rotated/cropped generated PDF is also selectable through PDF.js.
        rotated=make_japanese_fixture('simple',rotation=90,crop=True)
        rsid=context.request.post(base+'/api/sources/pdf',multipart={'file':{'name':'rotated.pdf','mimeType':'application/pdf','buffer':rotated}}).json()['source']['id']
        context.request.post(base+f'/api/sources/{rsid}/japanese-pdf/generate',data={'pages':[1]})
        page.goto(base+'/#/read/'+rsid)
        rotated_controls=page.locator('.japanese-pdf-controls')
        expect(rotated_controls.locator('.japanese-pdf-status')).to_contain_text('日本語試作・一部ページのみ翻訳済み',timeout=30000)
        rotated_controls.get_by_role('button',name='日本語PDF（試作）',exact=True).click()
        page.wait_for_function("document.querySelector('.pdf-page--ready .textLayer')?.textContent.includes('合成研究')")
        page.screenshot(path=str(output/'browser-japanese-rotated.png'))
        cases.append('rotated CropBox Japanese PDF.js TextLayer')
        # Figure/equation raster evidence, generated independently of the live viewer.
        visual=make_japanese_fixture('reader_detail')
        vsid=context.request.post(base+'/api/sources/pdf',multipart={'file':{'name':'visual.pdf','mimeType':'application/pdf','buffer':visual}}).json()['source']['id']
        context.request.post(base+f'/api/sources/{vsid}/japanese-pdf/generate',data={'pages':[1,2]})
        import time
        for attempt in range(300):
            visual_status=context.request.get(base+f'/api/sources/{vsid}/japanese-pdf').json()
            if visual_status['state']!='generating': break
            time.sleep(.1)
        assert visual_status['state']=='preview_ready',visual_status
        visual_pdf=context.request.get(base+f'/api/sources/{vsid}/japanese-pdf/{visual_status["generation_id"]}/file').body()
        with fitz.open(stream=visual,filetype='pdf') as a,fitz.open(stream=visual_pdf,filetype='pdf') as b:
            pixmaps=[d[0].get_pixmap(dpi=200) for d in (a,b)]
            images=[Image.frombytes('RGB',(px.width,px.height),px.samples) for px in pixmaps]
            pair=Image.new('RGB',(images[0].width*2,images[0].height),'white')
            pair.paste(images[0],(0,0));pair.paste(images[1],(images[0].width,0));pair.thumbnail((1600,1600))
            pair.save(output/'comparison-figure-equation.png')
        assert not errors,errors
        browser.close()
    with fitz.open(stream=original,filetype='pdf') as a,fitz.open(stream=pdf,filetype='pdf') as b:
        for number in range(3):
            images=[]
            for label,doc in [('english',a),('japanese',b)]:
                pix=doc[number].get_pixmap(dpi=200,alpha=False)
                pix.save(str(output/f'{label}-page-{number+1}.png'))
                images.append(Image.frombytes('RGB',(pix.width,pix.height),pix.samples))
            pair=Image.new('RGB',(images[0].width*2,images[0].height),'white')
            pair.paste(images[0],(0,0));pair.paste(images[1],(images[0].width,0))
            pair.thumbnail((1600,1600));pair.save(output/f'comparison-page-{number+1}.png')
    report={'passed':len(cases),'cases':cases,'pageerrors':errors,'original_hash':hashlib.sha256(original).hexdigest(),
            'pdf_hash':hashlib.sha256(pdf).hexdigest(),'generation_report':status['report'],'figure_equation_report':visual_status['report']}
    (output/'browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':len(cases),'cases':cases,'pageerrors':errors,'counts':status['report']['counts']},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--chromium',default=shutil.which('chromium'));parser.add_argument('--output',type=Path,default=Path(tempfile.gettempdir())/'kg-japanese-pdf-smoke')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with mock_server() as base: run(base,args.chromium,args.output)
