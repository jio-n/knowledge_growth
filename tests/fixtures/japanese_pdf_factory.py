"""Original synthetic layouts for partial Japanese PDF acceptance (no real papers)."""
import fitz
from tests.fixtures.pdf_factory import make_pdf, _text

ABSTRACT = 'We study a synthetic method for reproducible research. The method maps input to output and preserves the original evidence.'
BODY = 'The synthetic method processes input data in two stages. A frozen backbone is evaluated in a few-shot setting. The experiment uses generated data only.'


def make_japanese_fixture(kind='layout', rotation=0, crop=False):
    if kind in ('simple', 'two_column', 'reader_detail'):
        return make_pdf(kind, rotation=rotation, crop=crop)
    if kind=='reflow_demo':
        with fitz.open() as doc:
            page=doc.new_page(width=595,height=842)
            _text(page,(40,35,555,80),'Abstract',size=18,bold=True)
            _text(page,(40,100,555,210),ABSTRACT)
            for x,label in ((45,'Input'),(220,'Encoder'),(395,'Output')):
                page.draw_rect((x,220,x+140,300),color=(0,0,0),fill=(.9,.94,1))
                _text(page,(x+12,248,x+128,285),label,size=14)
            for x in (185,360): page.draw_line((x,260),(x+35,260))
            _text(page,(40,325,555,365),'Figure 1. Synthetic input-encoder-output architecture.')
            _text(page,(40,405,555,480),'As shown in Figure 1, the encoder maps input to output.\nA frozen backbone is evaluated in a few-shot setting.')
            columns=fitz.open(stream=make_pdf('two_column'),filetype='pdf')
            doc.insert_pdf(columns,from_page=0,to_page=0);columns.close()
            _text(doc[1],(100,730,520,790),'y = W x + b (1)',size=14)
            table=fitz.open(stream=make_pdf('reader_detail'),filetype='pdf')
            doc.insert_pdf(table,from_page=1,to_page=1);table.close()
            if crop: doc[0].set_cropbox((20,20,575,822))
            doc[0].set_rotation(rotation)
            return doc.tobytes(garbage=4,deflate=True)
    with fitz.open() as doc:
        page = doc.new_page(width=595, height=842)
        if kind=='long_reflow':
            _text(page,(40,35,555,80),'Abstract',size=18,bold=True)
            _text(page,(40,100,555,500),' '.join([ABSTRACT]*39),size=6)
        elif kind == 'no_text':
            page.draw_rect((50, 50, 300, 300), color=(0, 0, 0), fill=(.2, .5, .8))
        else:
            _text(page, (40, 35, 555, 80), 'Abstract', size=18, bold=True)
            _text(page, (40, 100, 555, 210), ABSTRACT)
            if kind in ('near', 'overlap', 'symbol', 'unruled', 'missing_glyph', 'overflow'):
                _text(page, (40, 300, 300, 400), BODY)
            if kind == 'near':
                page.draw_line((40, 299), (300, 299), width=.5)
                pix = fitz.Pixmap(fitz.csRGB, (0, 0, 30, 30), False); pix.clear_with(80)
                page.insert_image((40, 350, 300, 400), stream=pix.tobytes('png'))
            elif kind == 'overlap':
                _text(page, (50, 302, 290, 365), 'Overlapping text must stay original.', size=10)
            elif kind == 'symbol':
                _text(page, (40, 450, 400, 490), 'Loss = x + y (1)', size=18, bold=True)
            elif kind == 'unruled':
                for y, cells in [(470, ['Model', 'Accuracy', 'Time']), (490, ['SyntheticGPT', '91.012345', '0.123456']), (510, ['LoRA-ViT', '92.987654', '0.345678'])]:
                    for x, text in zip((40, 200, 350), cells):
                        _text(page, (x, y, x+140, y+18), text, size=10)
            if kind == 'layout':
                second = doc.new_page(width=595, height=842)
                _text(second, (40, 35, 555, 80), '1 Introduction', size=18, bold=True)
                for x in (40, 310):
                    for y in (110, 250, 390): _text(second, (x, y, x+245, y+125), BODY)
                visual = fitz.open(stream=make_pdf('reader_detail'), filetype='pdf')
                doc.insert_pdf(visual, from_page=1, to_page=1)
                visual.close()
                fourth = doc.new_page(width=595, height=842)
                _text(fourth, (40, 35, 555, 80), '3 Conclusion', size=18, bold=True)
                _text(fourth, (40, 110, 555, 210), ABSTRACT)
                # A mixed title and an equation must remain original.
                _text(fourth, (40, 400, 400, 450), 'Loss = W x + b (1)', size=18, bold=True)
        if crop: doc[0].set_cropbox((20, 20, 575, 822))
        doc[0].set_rotation(rotation)
        return doc.tobytes(garbage=4, deflate=True)


def make_translation_diagnostic_fixture():
    from tests.fixtures.japanese_translation_cases import TITLE, PARAGRAPH, CAPTION
    with fitz.open() as doc:
        p=doc.new_page(width=595,height=842)
        _text(p,(40,35,555,105),TITLE,size=18,bold=True)
        _text(p,(40,135,555,230),PARAGRAPH,size=11)
        p.draw_rect((40,280,555,355),color=(0,0,0))
        _text(p,(60,305,500,335),'GPT-4o   91.012345   MMAD',size=11)
        _text(p,(40,380,555,430),CAPTION,size=11)
        _text(p,(40,480,300,525),'y = W x + b (1)',size=14)
        return doc.tobytes()
