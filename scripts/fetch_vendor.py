"""Fetch vendored client libraries (pdf.js, marked) into client/vendor/.

Run once after clone: python scripts/fetch_vendor.py
Pinned versions for reproducibility. Files are gitignored (re-fetchable).
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

VENDOR = Path(__file__).resolve().parent.parent / "client" / "vendor"

FILES = {
    # pdf.js 4.x ES modules (legacy build works without bundler)
    "pdf.mjs": "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.5.136/build/pdf.min.mjs",
    "pdf.worker.mjs": "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.5.136/build/pdf.worker.min.mjs",
    # marked 12.x ES module
    "marked.esm.js": "https://cdn.jsdelivr.net/npm/marked@12.0.2/lib/marked.esm.js",
}


def main() -> int:
    VENDOR.mkdir(parents=True, exist_ok=True)
    ok = True
    for name, url in FILES.items():
        dest = VENDOR / name
        if dest.exists() and dest.stat().st_size > 1000:
            print(f"skip (exists): {name}")
            continue
        print(f"fetch: {name} <- {url}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "fetch_vendor/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                dest.write_bytes(r.read())
            print(f"  ok ({dest.stat().st_size} bytes)")
        except Exception as e:
            ok = False
            print(f"  FAILED: {e}\n  ({url} を手動でダウンロードして {dest} へ置いてください)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
