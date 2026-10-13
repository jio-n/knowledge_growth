"""Conservative local replacement; no moving figures, white paint or OCR.

A block is committed only after a scratch-page placement succeeds. Final 200dpi
pixel validation rejects every change outside the permitted original text boxes.
"""
from collections import Counter
import html
import math
from pathlib import Path
import re
import statistics
import tempfile
import fitz
from PIL import Image, ImageChops, ImageDraw
from .config import ROOT
from .japanese_translation import MAX_BLOCK_CHARS, MAX_UNITS, PDFError

MIN_FONT = 8.5
MIN_SCALE = .85
DPI = 200
MAX_RASTER_PIXELS = 16_000_000
MATH = re.compile(r'[=∑∫√∞±×÷≤≥≠∂∇²³α-ωΑ-Ω]|[\^_{}]|\\(?:frac|sum|begin)')


def expanded(rect, margin=1):
    return fitz.Rect(rect.x0-margin, rect.y0-margin, rect.x1+margin, rect.y1+margin)


def pixel_image(page):
    if page.rect.width * page.rect.height * (DPI/72)**2 > MAX_RASTER_PIXELS:
        raise PDFError('page_too_large_for_validation')
    pix = page.get_pixmap(dpi=DPI, colorspace=fitz.csRGB, alpha=False, annots=True)
    return Image.frombytes('RGB', (pix.width, pix.height), pix.samples)


def compare_pixels(before, after, rects):
    a, b = pixel_image(before), pixel_image(after)
    if a.size != b.size: raise PDFError('page_geometry_changed')
    difference = ImageChops.difference(a, b).convert('RGB')
    # Max-channel difference, rather than luminance rounding small changes away.
    channels = difference.split()
    changed = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2]).point(lambda x: 255 if x else 0)
    allowed = Image.new('L', a.size)
    draw = ImageDraw.Draw(allowed)
    for rect in rects:
        r = fitz.Rect(rect) * before.rotation_matrix
        s = DPI/72
        # 0.5pt tolerance for raster antialiasing, never for content layout.
        draw.rectangle((math.floor((r.x0-.5)*s), math.floor((r.y0-.5)*s),
                        math.ceil((r.x1+.5)*s), math.ceil((r.y1+.5)*s)), fill=255)
    outside = ImageChops.subtract(changed, allowed)
    return {'changed_pixels': changed.histogram()[255], 'outside_pixels': outside.histogram()[255],
            'dpi': DPI}


def page_units(page, blocks):
    bounds = fitz.Rect(0, 0, page.cropbox.width, page.cropbox.height)
    raw = page.get_text('dict', flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
    lines = [line for b in raw['blocks'] if b['type'] == 0 for line in b['lines']]
    spans = [s for line in lines for s in line['spans'] if s['text'].strip()]
    protected = [expanded(fitz.Rect(i['bbox']), 2) for i in page.get_image_info()]
    # Every drawing is protected, even small/zero-width lines omitted by ingest.
    protected += [expanded(fitz.Rect(d['rect']), 2) for d in page.get_drawings()]
    protected += [expanded(fitz.Rect(b['bbox']['rect']), 1) for b in blocks
                  if b.get('bbox') and b.get('role') in ('figure_candidate','table_candidate','equation_candidate')]
    # Detect ruled and unruled tables. Candidate flags alone are insufficient.
    try:
        protected += [expanded(fitz.Rect(t.bbox), 2) for t in page.find_tables().tables]
    except Exception:
        # No uncertain geometry is translated after table detection failed.
        return [dict(block=b, status='skipped_unsafe', reason='table_detection_failed') for b in blocks]
    # Additional unruled-cell guard: aligned short fragments with numeric cells.
    # Protect the whole row, including a model name printed at heading size.
    rows = []
    for line in lines:
        text = ''.join(s['text'] for s in line['spans']).strip()
        r = fitz.Rect(line['bbox'])
        row = next((row for row in rows if abs(row[0][0].y0-r.y0) < 2), None)
        if row is None: rows.append([(r, text)])
        else: row.append((r, text))
    for row in rows:
        if len(row) >= 2 and all(len(t) < 80 for _,t in row) and any(re.fullmatch(r'[+-]?\d+(?:\.\d+)?%?', t) for _,t in row):
            region = fitz.Rect(row[0][0])
            for r,_ in row[1:]: region |= r
            protected.append(expanded(region, 2))
    protected += [expanded(a.rect, 1) for a in page.annots() or []]
    protected += [expanded(fitz.Rect(link['from']), 1) for link in page.get_links()]
    units = []
    for b in blocks:
        unit = dict(block=b, status='pending', reason=None)
        units.append(unit)
        geometry = b.get('bbox')
        if not geometry or geometry.get('coordinate_system') != 'pymupdf_unrotated':
            unit.update(status='skipped_unsafe', reason='missing_geometry'); continue
        rect = fitz.Rect(geometry['rect'])
        if not b['text'].strip() or b.get('role') in ('figure_candidate','table_candidate','equation_candidate'):
            unit.update(status='skipped_protected', reason='visual_or_equation'); continue
        if MATH.search(b['text']):
            unit.update(status='skipped_protected', reason='math_or_symbols'); continue
        if any(rect.intersects(r) for r in protected):
            unit.update(status='skipped_protected', reason='image_drawing_table_or_annotation'); continue
        if rect.is_empty or not bounds.contains(rect) or len(b['text']) > MAX_BLOCK_CHARS:
            unit.update(status='skipped_unsafe', reason='invalid_or_large_geometry'); continue
        hit = [s for s in spans if rect.intersects(fitz.Rect(s['bbox']))]
        norm = lambda text: re.sub(r'\s+', '', text)
        if norm(''.join(s['text'] for s in hit)) != norm(b['text']):
            unit.update(status='skipped_unsafe', reason='overlapping_or_ambiguous_text'); continue
        if any(not expanded(rect, .01).contains(fitz.Rect(s['bbox'])) for s in hit):
            unit.update(status='skipped_unsafe', reason='crossing_text'); continue
        if any(rect.intersects(fitz.Rect(line['bbox'])) and tuple(line['dir']) != (1., 0.) for line in lines):
            unit.update(status='skipped_unsafe', reason='nonhorizontal_text'); continue
        if any((s['flags'] & 1) or re.search(r'math|symbol|dingbat|cmmi|cmsy|cmex', s['font'], re.I) for s in hit):
            unit.update(status='skipped_protected', reason='math_font'); continue
        # Labels, isolated numbers and compact cell-like fragments are ambiguous.
        if len(b['text'].split()) < 4 and b['kind'] != 'heading' and b.get('role') not in ('figure_caption','table_caption'):
            unit.update(status='skipped_unsafe', reason='short_label_or_cell'); continue
        if any(other['id'] != b['id'] and other.get('bbox') and rect.intersects(fitz.Rect(other['bbox']['rect']))
               for other in blocks if other.get('role') not in ('figure_candidate','table_candidate')):
            unit.update(status='skipped_unsafe', reason='overlapping_blocks'); continue
        unit['font_size'] = max(MIN_FONT, statistics.median(s['size'] for s in hit))
    return units


def detect_units(document, blocks, pages):
    units = []
    for number in pages:
        units += page_units(document[number-1], [b for b in blocks if b['page'] == number])
    if len(units) > MAX_UNITS:
        raise PDFError('too_many_blocks_select_fewer_pages')
    return units


def place_block(document, unit, font, archive):
    b, text = unit['block'], unit['text']
    rect = fitz.Rect(b['bbox']['rect'])
    if not re.search(r'[\u3040-\u30ff\u3400-\u9fff]', text):
        unit.update(status='failed', reason='cached_not_japanese'); return
    if any(ch in '□■�' or (not ch.isspace() and not font.has_glyph(ord(ch))) for ch in text):
        unit.update(status='failed', reason='missing_glyph'); return
    # Work on a scratch page: overflow or exceptions never redact the real output.
    with fitz.open() as trial:
        trial.insert_pdf(document, from_page=b['page']-1, to_page=b['page']-1)
        page = trial[0]
        # PyMuPDF rotated + cropped page matrices omit the crop offset in some
        # insertion paths. Work in the documented unrotated coordinate space.
        page.set_rotation(0)
        try:
            page.add_redact_annot(rect, fill=False, cross_out=False)
            page.apply_redactions(images=0, graphics=0, text=0)
            if page.get_text('text', clip=rect).strip():
                unit.update(status='failed', reason='original_text_remains'); return
            size = unit['font_size']
            css = ('@font-face {font-family: KGJapanese; src: url(kg-cjk.ttf);}'
                   f'* {{font-family: KGJapanese; font-size: {size}pt; line-height: 1.4; margin: 0; padding: 0;}}')
            content = '<div>' + html.escape(text) + '</div>'
            spare, scale = page.insert_htmlbox(rect, content, css=css, archive=archive,
                                               scale_low=max(MIN_SCALE, MIN_FONT/size))
            if spare < 0:
                unit.update(status='overflow', reason='minimum_readable_size'); return
            norm = lambda t: re.sub(r'\s+', '', t)
            if norm(text) not in norm(page.get_text('text', clip=rect)):
                unit.update(status='failed', reason='text_extraction_mismatch'); return
            # Glyph extents must remain inside the original box, not just visually fit.
            new = [s for block in page.get_text('dict')['blocks'] if block['type'] == 0
                   for line in block['lines'] for s in line['spans'] if rect.intersects(fitz.Rect(s['bbox']))]
            if any(not expanded(rect, .1).contains(fitz.Rect(s['bbox'])) for s in new):
                unit.update(status='overflow', reason='glyph_outside_bbox'); return
            # Commit the validated scratch content, keeping page boxes/rotation.
            target = document[b['page']-1]
            rotation = target.rotation
            target.set_rotation(0)
            target.add_redact_annot(rect, fill=False, cross_out=False)
            target.apply_redactions(images=0, graphics=0, text=0)
            target.insert_htmlbox(rect, content, css=css, archive=archive,
                                  scale_low=max(MIN_SCALE, MIN_FONT/size))
            target.set_rotation(rotation)
            unit.update(status='translated', reason=None, placed_font_size=round(size*scale, 2))
        except Exception:
            # Final outside-box validation also rejects any partially committed page.
            raise PDFError('placement_failed') from None


def generate_pdf(original, units, output_path):
    with fitz.open(stream=original, filetype='pdf') as before, fitz.open(stream=original, filetype='pdf') as after:
        font = fitz.Font('cjk')  # Droid Sans Fallback (Apache 2.0), bundled by PyMuPDF.
        with tempfile.TemporaryDirectory(prefix='kg-font-') as directory:
            Path(directory, 'kg-cjk.ttf').write_bytes(font.buffer)
            archive = fitz.Archive(directory)
            for unit in units:
                if unit['status'] == 'pending' and unit.get('text'):
                    place_block(after, unit, font, archive)
        translated = [u for u in units if u['status'] == 'translated']
        if not translated: raise PDFError('no_safe_translated_blocks')
        notice_name = 'knowledge_growth-japanese-font-NOTICE.txt'
        if notice_name in after.embfile_names():
            notice_name = f'knowledge_growth-japanese-font-NOTICE-{after.embfile_count()}.txt'
        after.embfile_add(notice_name, (ROOT / 'docs/licenses/droid-NOTICE.txt').read_bytes(),
                          desc='Droid Sans Fallback: Apache 2.0 license and attribution; font embedded by knowledge_growth')
        # Keep original font resources untouched, including unselected pages.
        # Story embeds the CJK font; garbage=4 deduplicates identical streams.
        after.save(output_path, garbage=4, deflate=True)
    # Validate the actual serialized file, not only its in-memory predecessor.
    with fitz.open(stream=original, filetype='pdf') as before, fitz.open(output_path) as after:
        if len(before) != len(after): raise PDFError('page_count_changed')
        quality = []
        selected = {u['block']['page'] for u in units}
        for number, (a, b) in enumerate(zip(before, after), 1):
            if (a.mediabox, a.cropbox, a.rotation) != (b.mediabox, b.cropbox, b.rotation):
                raise PDFError('page_geometry_changed')
            allowed = [u['block']['bbox']['rect'] for u in translated if u['block']['page'] == number]
            if number in selected:
                diff = compare_pixels(a, b, allowed)
                if diff['outside_pixels']: raise PDFError('changes_outside_text_bbox')
                quality.append({'page': number, **diff})
            else:
                # Object numbers may be renumbered by garbage collection. Compare bytes.
                if [before.xref_stream(x) for x in a.get_contents()] != [after.xref_stream(x) for x in b.get_contents()]:
                    raise PDFError('unselected_page_changed')
            for u in translated:
                if u['block']['page'] != number: continue
                norm = lambda t: re.sub(r'\s+', '', t)
                if norm(u['text']) not in norm(b.get_text('text', clip=fitz.Rect(u['block']['bbox']['rect']))):
                    raise PDFError('serialized_text_mismatch')
        return {'selected_pages': sorted(selected), 'counts': dict(Counter(u['status'] for u in units)),
                'translated_pages': sorted({u['block']['page'] for u in translated}),
                'text_blocks': sum(bool(u['block']['text'].strip()) for u in units),
                'translated_ratio': len(translated)/sum(bool(u['block']['text'].strip()) for u in units), 'quality': quality,
                'units': [{'block_id': u['block']['id'], 'translation_id': u.get('translation_id'),
                           'page': u['block']['page'], 'bbox': u['block'].get('bbox'),
                           'status': u['status'], 'reason': u['reason'],
                           'font_size': u.get('placed_font_size')} for u in units]}
