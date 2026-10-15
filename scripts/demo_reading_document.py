"""Generate the original synthetic preview, using mock HTTP only. No live arXiv calls."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from app.reading_document.acquire import Fetcher
from app.reading_document.snapshot import snapshot, write_json, preview
from tests.fixtures.reading_html_factory import PINNED, transport

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="reading-output/demo")
    args = parser.parse_args()
    with httpx.Client(transport=transport()) as client:
        output, report = snapshot(PINNED, args.output, fetcher=Fetcher(client, interval=0))
    report.update(fixture_only=True, live_network_verified=False,
                  fixture_notice="Original generated HTML/image. Synthetic arXiv ID, mock HTTP; not MMAD.")
    write_json(output / "acquisition_report.json", report)
    document = json.loads((output / "reading_document.json").read_text(encoding="utf-8"))
    document["source"]["fixture_only"] = True
    write_json(output / "reading_document.json", document)
    (output / "english_preview.html").write_text(preview(document, report), encoding="utf-8")
    print(f"{output / 'english_preview.html'} ({report['status']}; synthetic only)")
