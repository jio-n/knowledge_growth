"""Original synthetic HTML and image, reusable for tests/demo (no paper content)."""
import fitz
import httpx

PINNED = "2601.00001v2"  # synthetic identifier for mock transport ONLY
URL = f"https://arxiv.org/html/{PINNED}"


def image():
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=160)
        for x, label in ((20, "Input"), (225, "Encoder"), (430, "Output")):
            page.draw_rect(fitz.Rect(x, 40, x + 150, 120), color=(0.1, 0.3, 0.5), fill=(0.9, 0.94, 1))
            page.insert_text((x + 35, 88), label, fontsize=18)
        for x in (170, 375):
            page.draw_line((x, 80), (x + 55, 80), color=(0.1, 0.3, 0.5))
        return page.get_pixmap().tobytes("png")


def html(extra=""):
    return f'''<!doctype html><html><head><title>Original synthetic reading fixture</title>
<link rel="canonical" href="{URL}"><base href="/html/{PINNED}/"></head><body>
<article class="ltx_document" id="paper">
<h1 id="title">Synthetic Reading Document</h1>
<div class="ltx_authors">Fixture Author · Original generated material, no real research claims</div>
<div class="ltx_abstract" id="abstract"><h2>Abstract</h2>
<p id="abs-p1">This original synthetic article tests a reading document. It retains paragraphs, figures, table cells, and equations.</p>
<p id="abs-p2">The numbers below are invented test values. No translation or AI service is used.</p></div>
<section id="S1"><h2 id="S1-title">1 Introduction</h2>
<p id="S1-p1">Read in source order. The encoder maps input to output in <a href="#F1">Figure 1</a>. See <a href="#T1">Table 1</a> and <a href="#bib1">[1]</a>.</p>
<p>Exact values: 91.25%, 0.001, ID-A_7; frozen backbone; few-shot.</p>
<ul id="L1"><li>Preserve the first item.</li><li>Preserve the second item with <em>emphasis</em>.</li></ul>
<figure id="F1"><img src="figures/pipeline.png" alt="Input to encoder to output">
<figcaption id="F1-caption"><span class="ltx_tag">Figure 1.</span> Original generated input–encoder–output diagram.</figcaption></figure>
<figure class="ltx_table" id="T1"><figcaption><span class="ltx_tag">Table 1.</span> Invented evaluation scores.</figcaption>
<table><thead><tr><th rowspan="2">Method</th><th colspan="2">Synthetic benchmark</th></tr><tr><th>Accuracy</th><th>Count</th></tr></thead>
<tbody><tr><td>Baseline</td><td>89.50</td><td>200</td></tr><tr><td>Proposed</td><td>91.25</td><td>200</td></tr></tbody></table></figure>
<div class="ltx_equation" id="E1"><math display="block"><semantics><mrow><mi>y</mi><mo>=</mo><mi>W</mi><mi>x</mi><mo>+</mo><mi>b</mi></mrow>
<annotation encoding="application/x-tex">y = W x + b</annotation></semantics></math><span class="ltx_tag">(1)</span></div>
<aside class="ltx_note" id="note1">Footnote: test content only.</aside>
{extra}</section>
<section class="ltx_appendix" id="A1"><h2>Appendix A: Reproducibility</h2><p id="A1-p1">All words and images were generated for this repository.</p></section>
<section class="ltx_bibliography" id="bib"><h2>References</h2><ul class="ltx_biblist"><li class="ltx_bibitem" id="bib1">[1] Fixture Author. Synthetic reference. 2026. Unrelated ID 2410.09453v3 is not the document identity.</li></ul></section>
</article></body></html>'''


def transport(markup=None):
    def handle(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if str(request.url) == URL:
            return httpx.Response(200, text=markup if markup is not None else html(), headers={"content-type": "text/html"})
        if str(request.url) == URL + "/figures/pipeline.png":
            return httpx.Response(200, content=image(), headers={"content-type": "image/png"})
        return httpx.Response(404)
    return httpx.MockTransport(handle)
