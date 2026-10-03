"""Reproducible, original PDFs generated entirely with PyMuPDF and built-in fonts."""
import fitz


FIXTURE_NAMES = ("simple", "two_column", "visual_evidence")


def _text(page, rect, text, *, size=11, bold=False):
    remaining = page.insert_textbox(fitz.Rect(rect), text, fontsize=size,
                                    fontname="hebo" if bold else "helv")
    if remaining < 0:
        raise ValueError(f"Fixture text does not fit: {text[:60]}")


def make_pdf(name: str = "simple") -> bytes:
    if name not in FIXTURE_NAMES:
        raise ValueError(f"Unknown PDF fixture: {name}")
    with fitz.open() as doc:
        doc.set_metadata({
            "title": f"Synthetic Research Fixture: {name}",
            "author": "Fixture Author A; Fixture Author B",
            "subject": "Original synthetic test content; not a research publication",
            "creator": "knowledge_growth generated fixtures",
            "creationDate": "D:20261004000000Z",
            "modDate": "D:20261004000000Z",
        })
        first = doc.new_page(width=595, height=842)
        _text(first, (40, 32, 555, 78), "Synthetic Research Fixture", size=20, bold=True)
        if name == "simple":
            _text(first, (40, 92, 555, 128), "1 Introduction", size=16, bold=True)
            _text(first, (40, 145, 555, 300),
                  "This generated document tests paper registration and reading.\n"
                  "It contains original synthetic text with no external paper content.\n"
                  "The synthetic method processes an input and returns an output.")
        elif name == "two_column":
            # Deliberately insert the right column first: future reading-order tests
            # must use geometry, rather than the order of PDF drawing commands.
            for x, label in ((310, "RIGHT"), (40, "LEFT")):
                _text(first, (x, 92, x + 245, 132), f"1 {label} Column", size=16, bold=True)
                for i in range(8):
                    y = 148 + i * 65
                    _text(first, (x, y, x + 245, y + 60),
                          f"{label} paragraph {i + 1}.\nSynthetic column content for reading order.")
        else:
            _text(first, (40, 92, 555, 128), "1 Architecture", size=16, bold=True)
            for x, label in ((45, "Input"), (220, "Encoder"), (395, "Output")):
                first.draw_rect(fitz.Rect(x, 180, x + 140, 260), color=(0, 0, 0),
                                fill=(0.9, 0.94, 1))
                _text(first, (x + 12, 208, x + 128, 245), label, size=14)
            for x in (185, 360):
                first.draw_line((x, 220), (x + 35, 220))
                first.draw_line((x + 29, 214), (x + 35, 220))
                first.draw_line((x + 29, 226), (x + 35, 220))
            _text(first, (40, 285, 555, 335), "Figure 1. Synthetic input-encoder-output architecture.")
            _text(first, (40, 365, 555, 420),
                  "As shown in Figure 1, the encoder maps input to output.\n"
                  "A frozen backbone is evaluated in a few-shot setting.")
            _text(first, (100, 470, 520, 510), "y = W x + b    (1)", size=14)
            _text(first, (40, 540, 555, 610),
                  "Equation (1) is a synthetic linear mapping.\n"
                  "Shared evidence phrase appears on both pages.")
        second = doc.new_page(width=595, height=842)
        _text(second, (40, 40, 555, 80), "2 Results", size=16, bold=True)
        if name == "visual_evidence":
            _text(second, (40, 100, 555, 135), "Table 1. Synthetic benchmark scores (invented test values).")
            rows = ["Dataset | Metric | Setting | Baseline | Proposed"] + [
                f"Synthetic-{i:02d} | Accuracy | few-shot | {60 + i}.0 | {62 + i}.0"
                for i in range(12)
            ]
            for i, text in enumerate(rows):
                y = 145 + i * 32
                second.draw_rect(fitz.Rect(40, y, 555, y + 32), color=(0.4, 0.4, 0.4))
                _text(second, (48, y + 7, 548, y + 31), text, size=10)
            _text(second, (40, 595, 555, 655),
                  "Table 1 contains invented scores for test use only.\n"
                  "Shared evidence phrase appears on both pages.")
        else:
            _text(second, (40, 110, 555, 250),
                  "The synthetic experiment produces a reproducible test result.\n"
                  "This second page checks page numbering and navigation.\n"
                  "No real benchmark performance is claimed.")
        _text(second, (40, 715, 555, 755), "3 Conclusion", size=16, bold=True)
        _text(second, (40, 765, 555, 815), "This fixture is for offline regression testing only.")
        return doc.tobytes(garbage=4, deflate=True, no_new_id=True)
