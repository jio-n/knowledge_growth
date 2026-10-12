"""Reader quality/performance acceptance with generated PDFs and an isolated mock DB.

Optional development dependencies: Playwright + Chromium. Never calls a paid API.
"""
import argparse
import json
import math
from pathlib import Path
import shutil
import tempfile

from smoke_import_brief_browser import mock_server
from playwright.sync_api import sync_playwright, expect
from tests.fixtures.pdf_factory import make_pdf


POSITION = """() => {
  const c=document.querySelector('.pdf-scroll'), box=c.getBoundingClientRect();
  const cy=box.top+c.clientHeight/2;
  const pages=[...c.querySelectorAll('.pdf-page')].map(p=>({p,b:p.getBoundingClientRect()}));
  const hit=pages.find(({b})=>b.top<=cy&&b.bottom>=cy);
  return hit ? {page:Number(hit.p.dataset.pdfPage),y:(cy-hit.b.top)/hit.b.height} : null;
}"""
RASTERS = """() => [...document.querySelectorAll('.pdf-page canvas')].map(c=>({
  width:c.width,height:c.height,cssWidth:parseFloat(c.style.width),cssHeight:parseFloat(c.style.height)
}))"""


def run(base, chromium, output, baseline_url=None):
    cases, metrics, errors = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium, headless=True, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, device_scale_factor=2)
        context.set_default_timeout(15000)
        page = context.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))

        def upload(name, **kwargs):
            response = context.request.post(base + '/api/sources/pdf', multipart={
                'file': {'name': name + '.pdf', 'mimeType': 'application/pdf', 'buffer': make_pdf(name, **kwargs)},
                'force': 'true'})
            assert response.ok, response.text()
            sid = response.json()['source']['id']
            doc = context.request.get(base + f'/api/sources/{sid}/document').json()
            page.goto(base + '/#/read/' + sid)
            page.wait_for_function("document.querySelector('.pdf-page--ready .textLayer span')")
            return sid, doc

        def ready(num):
            page.wait_for_selector(f'.pdf-page--ready[data-pdf-page="{num}"] .textLayer span', state='attached')

        def zoom(value):
            inp = page.get_by_role('spinbutton', name='PDF倍率（%）', exact=True)
            inp.fill(str(value)); inp.press('Enter')
            page.wait_for_function("v=>document.querySelector('.pdf-controls input').value===String(v)", arg=max(75, min(250, value)))

        def point(num, y=.5):
            page.evaluate("""({num,y})=>{
              const c=document.querySelector('.pdf-scroll'),p=c.querySelector(`.pdf-page[data-pdf-page="${num}"]`);
              const b=p.getBoundingClientRect(),box=c.getBoundingClientRect();
              c.scrollTop+=b.top-box.top+b.height*y-c.clientHeight/2;
            }""", {'num': num, 'y': y})
            ready(num)
            page.wait_for_timeout(200)  # persistence debounce

        def stays(before, tolerance=.025):
            after = page.evaluate(POSITION)
            assert before and after and before['page'] == after['page'], (before, after)
            assert abs(before['y'] - after['y']) < tolerance, (before, after)

        def raster_check(dpr):
            values = page.evaluate(RASTERS)
            assert values
            for v in values:
                assert v['width'] * v['height'] <= 6_000_000
                assert max(v['width'], v['height']) <= 8192
                expected = min(dpr, math.sqrt(6_000_000 / (v['cssWidth'] * v['cssHeight'])),
                               8192 / v['cssWidth'], 8192 / v['cssHeight'])
                assert abs(v['width'] / v['cssWidth'] - expected) < .01, v
                assert abs(v['height'] / v['cssHeight'] - expected) < .01, v
            metrics.append({'dpr': dpr, 'rasters': values})

        def table_shot(target, path):
            target.locator('.pdf-page[data-pdf-page="2"]').evaluate("""p=>{
              const c=p.querySelector('canvas'),scale=c.getBoundingClientRect().width/595;
              const region=document.createElement('div');region.className='table-screenshot-region';
              Object.assign(region.style,{position:'absolute',left:'0',top:`${95*scale}px`,
                width:`${595*scale}px`,height:`${540*scale}px`,pointerEvents:'none'});p.append(region);
            }""")
            target.locator('.table-screenshot-region').screenshot(path=str(path))
            target.locator('.table-screenshot-region').evaluate('e=>e.remove()')

        sid, doc = upload('reader_detail')
        point(2, .45)
        raster_check(2)
        table_shot(page, output / 'table-fit-hidpi.png')
        zoom(250); ready(2); raster_check(2)
        point(2, .33)
        before = page.evaluate(POSITION)
        zoom(175); ready(2); stays(before)
        page.get_by_role('button', name='PDFを拡大', exact=True).click(); ready(2)
        assert page.get_by_role('spinbutton', name='PDF倍率（%）', exact=True).input_value() == '200'
        stays(before)
        page.get_by_role('button', name='PDFを縮小', exact=True).click(); ready(2); stays(before)
        zoom(50); ready(2)
        assert page.get_by_role('spinbutton', name='PDF倍率（%）', exact=True).input_value() == '75'
        zoom(999); ready(2)
        assert page.get_by_role('spinbutton', name='PDF倍率（%）', exact=True).input_value() == '250'
        cases += ['DPR2 raster/CSS separation', '6MP/8192px bounds', '75–250% inputs and +/- rerender', 'zoom relative position']

        # Select tiny numeric table text at zoom, persist the original-coordinate anchor.
        span = page.locator('.pdf-page[data-pdf-page="2"] .textLayer span').filter(has_text='Synthetic-06').first
        span.scroll_into_view_if_needed()
        span.evaluate("""s=>{const r=document.createRange();r.selectNodeContents(s);
          const sel=getSelection();sel.removeAllRanges();sel.addRange(r);
          s.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));}""")
        page.locator('.selection-popover').get_by_role('button', name='ハイライト', exact=True).click()
        page.wait_for_function("!document.querySelector('.selection-popover')")
        current = context.request.get(base + f'/api/sources/{sid}/document').json()
        anchor = json.loads(current['highlights'][0]['anchor'])
        block = next(b for b in doc['blocks'] if 'Synthetic-06' in b['text'])
        assert anchor['blockId'] == block['id'] and anchor['page'] == 2, anchor
        assert anchor['sourceVersion'] == doc['version']['id'] and anchor['sourceHash'] == doc['version']['content_hash']
        assert abs(anchor['bbox']['rect'][1] - block['bbox']['rect'][1]) < 4
        page.evaluate("async a=>(await import('/js/views/reader.js')).resolveAnchor(a)", anchor)
        marker = page.locator('.pdf-evidence-marker'); expect(marker).to_be_visible()
        marker.evaluate("m=>m.style.borderStyle='dashed'")  # same candidate style used by Reader navigation
        box_before = marker.evaluate('m=>({left:parseFloat(m.style.left),width:parseFloat(m.style.width)})')
        page.evaluate("async()=>{(await import('/js/components/pdfviewer.js')).setZoom(1.5)}")
        box_after = marker.evaluate('m=>({left:parseFloat(m.style.left),width:parseFloat(m.style.width)})')
        assert marker.evaluate('m=>m.style.borderStyle') == 'dashed'
        assert abs(box_after['left'] / box_before['left'] - .6) < .01
        assert abs(box_after['width'] / box_before['width'] - .6) < .01
        ready(2)
        cases += ['zoomed tiny-table TextLayer -> saved Highlight provenance', 'Evidence bbox and marker rescale']

        # A collapsed pane keeps its mounted contents, scroll offsets and active tab.
        page.get_by_role('button', name='ノート', exact=True).click()
        point(2, .35)
        before = page.evaluate(POSITION)
        zoom(250); ready(2); stays(before)
        page.get_by_role('button', name='PDFを全幅で読む', exact=True).click(); ready(2); stays(before)
        expect(page.locator('.reader-pane-right')).to_be_hidden()
        assert page.locator('.reader-pane-left').bounding_box()['width'] > 1400
        page.screenshot(path=str(output / 'table-250-full.png'))
        page.reload(); ready(2); stays(before)
        expect(page.locator('.reader-pane-right')).to_be_hidden()
        assert page.get_by_role('spinbutton', name='PDF倍率（%）', exact=True).input_value() == '250'
        page.get_by_role('button', name='サイドパネルを表示', exact=True).click(); ready(2); stays(before)
        expect(page.locator('.tabs-header button[data-tab="note"]')).to_have_class('active')
        page.get_by_role('button', name='幅に合わせる', exact=True).click(); ready(2)
        before = page.evaluate(POSITION)
        divider = page.locator('.reader-divider').bounding_box()
        page.mouse.move(divider['x'] + 3, divider['y'] + 50); page.mouse.down()
        page.mouse.move(1000, divider['y'] + 50, steps=8); page.mouse.up()
        page.wait_for_timeout(300); ready(2); stays(before)
        assert abs(page.locator('.pdf-page[data-pdf-page="2"]').bounding_box()['width'] -
                   (page.locator('.pdf-scroll').evaluate('c=>c.clientWidth') - 48)) < 2
        before = page.evaluate(POSITION)
        page.get_by_role('button', name='PDFを全幅で読む', exact=True).click(); ready(2); stays(before)
        page.get_by_role('button', name='サイドパネルを表示', exact=True).click(); ready(2); stays(before)
        page.get_by_role('button', name='ページ全体', exact=True).click(); ready(2)
        full = page.locator('.pdf-page[data-pdf-page="2"]').bounding_box()
        viewport = page.locator('.pdf-scroll').bounding_box()
        assert full['y'] >= viewport['y'] and full['y'] + full['height'] <= viewport['y'] + viewport['height']
        page.get_by_role('button', name='テキスト表示', exact=True).click()
        expect(page.locator('.kg-highlight')).to_contain_text('Synthetic-06')
        page.reload(); expect(page.locator('.kg-highlight')).to_contain_text('Synthetic-06')
        page.get_by_role('button', name='PDF表示', exact=True).click(); ready(2)
        cases += ['full-width and tab preserved', 'reload zoom/pane/page/fraction restore', 'split resize follows fit-width',
                  'fit-width panel toggle position', 'fit-page whole page', 'Highlight/text-mode reload and PDF return']

        page.get_by_role('button', name='幅に合わせる', exact=True).click(); ready(1)
        point(1, .4)
        page.screenshot(path=str(output / 'figure-equation.png'))

        for name in ('simple', 'two_column'):
            _, _ = upload(name); zoom(150); ready(1); raster_check(2)
        # Rotated crop: TextLayer box must lie on the visible raster and reverse to source points.
        for rotation in (90, 180, 270):
            _, rotated_doc = upload('simple', rotation=rotation, crop=True)
            zoom(175); ready(1)
            span = page.locator('.pdf-page[data-pdf-page="1"] .textLayer span').filter(has_text='This generated document tests').first
            coords = span.evaluate("""async s=>{
              const r=document.createRange();r.selectNodeContents(s);
              return (await import('/js/components/pdfviewer.js')).selectionGeometry(1,r);
            }""")
            rotated_block = next(b for b in rotated_doc['blocks'] if 'This generated document tests' in b['text'])
            assert coords['page_rect'] == [0, 0, 555, 802] and coords['rotation'] == rotation
            assert abs(coords['rect'][0] - rotated_block['bbox']['rect'][0]) < 4, coords
            assert abs(coords['rect'][1] - rotated_block['bbox']['rect'][1]) < 4, coords
            if rotation == 90:
                page.screenshot(path=str(output / 'rotated-selection.png'))
        cases += ['normal and two-column PDF', '90/180/270-degree CropBox TextLayer round trip']

        sid, long_doc = upload('long_reader')
        assert page.locator('.pdf-page').count() == 60
        # Await only current and neighbors, never all 60 canvases.
        jump = page.get_by_role('spinbutton', name='PDFページ番号', exact=True)
        jump.fill('30'); jump.press('Enter'); ready(30)
        point(30, .3)
        last = next(b for b in long_doc['blocks'] if b['page'] == 60 and 'paragraph 6' in b['text'])
        result = page.evaluate("async a=>(await import('/js/views/reader.js')).resolveAnchor(a)",
                              {'sourceVersion': long_doc['version']['id'], 'blockId': last['id'], 'page': 60})
        assert result['status'] == 'resolved'
        ready(60); expect(page.locator('.pdf-evidence-marker')).to_be_visible()
        assert page.locator('.pdf-page canvas').count() <= 5
        assert page.locator('.pdf-page[data-pdf-page="1"] canvas').count() == 0
        point(1); raster_check(2)
        # Deterministic late completion after the actual PDF.js render has finished.
        page.evaluate("""async sid=>{
          const lib=await import('/vendor/pdf.mjs');
          const task=lib.getDocument({url:`/api/sources/${sid}/file`});
          const doc=await task.promise, proto=Object.getPrototypeOf(await doc.getPage(1));
          const original=proto.render;window.readerDelayedRenders=0;
          proto.render=function(...args){
            const render=original.apply(this,args);window.readerDelayedRenders++;
            return {cancel:()=>render.cancel(),promise:render.promise.then(()=>new Promise(resolve=>setTimeout(resolve,300)))};
          };
          window.restoreReaderRender=()=>{proto.render=original;};
          await task.destroy();
          (await import('/js/components/pdfviewer.js')).setZoom(1.75);
        }""", sid)
        page.wait_for_function('window.readerDelayedRenders>0')
        # Stress render cancellation and delayed-result isolation without waiting between operations.
        page.evaluate("""async()=>{
          const v=await import('/js/components/pdfviewer.js');
          for(let i=0;i<12;i++){v.scrollToPage(i*4+1);v.setZoom(i%2?2.5:.75);}
          v.scrollToPage(30);v.setZoom(1.5);
        }""")
        ready(30)
        assert page.locator('.pdf-page canvas').count() <= 5
        assert all(abs(v['cssWidth'] - 842 * 2) < 2 for v in page.evaluate(RASTERS)
                   if v['cssWidth'] > 1300)  # rotated landscape uses 150% of CSS point width
        before = page.evaluate(POSITION)
        page.get_by_role('button', name='テキスト表示', exact=True).click()
        page.wait_for_function("!document.querySelector('.pdf-page canvas')")
        page.get_by_role('button', name='PDF表示', exact=True).click(); ready(30); stays(before)
        # Navigate away while rendering, then to a different PDF; late work cannot mutate it.
        page.evaluate("async()=>{const v=await import('/js/components/pdfviewer.js');v.setZoom(2.5);location.hash='#/';}")
        page.wait_for_selector('.reader', state='detached')
        upload('reader_detail'); ready(1)
        assert page.locator('.pdf-page').count() == 2
        assert page.evaluate('window.readerDelayedRenders') > 1
        page.evaluate('window.restoreReaderRender()')
        cases += ['60-page shells, at most 5 rasters, eviction and rerender', 'far rotated-page Evidence navigation',
                  'rapid page/zoom cancellation with 300ms delayed completions', 'hidden PDF releases rasters', 'route teardown isolates late renders']

        # Simulate monitor densities independently; extreme DPR proves cap enforcement.
        for dpr in (1, 3, 8):
            other = browser.new_context(viewport={'width': 1440, 'height': 1000}, device_scale_factor=dpr)
            other_page = other.new_page(); other_page.on('pageerror', lambda e: errors.append(str(e)))
            other_page.goto(base + '/#/read/' + sid)
            other_page.wait_for_selector('.pdf-page--ready .textLayer span', state='attached')
            original_page = page; page = other_page
            raster_check(dpr)
            other.close(); page = original_page
        cases.append('DPR1/3/8 and extreme-density bound')

        if baseline_url:
            # Optional visual reference at the same CSS size and DPR, served from latest main.
            response = context.request.post(baseline_url + '/api/sources/pdf', multipart={
                'file': {'name': 'reader_detail.pdf', 'mimeType': 'application/pdf', 'buffer': make_pdf('reader_detail')},
                'force': 'true'})
            before_page = context.new_page()
            before_page.goto(baseline_url + '/#/read/' + response.json()['source']['id'])
            before_page.wait_for_function("document.querySelectorAll('.textLayer span').length>15 && document.querySelectorAll('canvas').length>=2")
            table_shot(before_page, output / 'table-main-before.png')
            old = before_page.locator('.pdf-page canvas').first.evaluate('c=>({pixels:c.width,css:c.getBoundingClientRect().width})')
            assert abs(old['pixels'] / old['css'] - 1) < .01
            cases.append('latest-main DPR2 comparison (old raster ratio 1, new 2)')
        browser.close()
    assert not errors, errors
    report = {'passed': len(cases), 'cases': cases, 'metrics': metrics, 'pageerrors': errors}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium', default=shutil.which('chromium'))
    parser.add_argument('--output-dir', type=Path, default=Path(tempfile.gettempdir()) / 'kg-reader-browser')
    parser.add_argument('--baseline-url', help='Optional latest-main mock server for before screenshot')
    args = parser.parse_args(); args.output_dir.mkdir(parents=True, exist_ok=True)
    with mock_server() as base:
        run(base, args.chromium, args.output_dir, args.baseline_url)
