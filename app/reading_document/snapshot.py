"""Filesystem-only output and verified cache. This module never opens a database."""
from __future__ import annotations

import json
import re
import warnings
from io import BytesIO
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from PIL import Image

from . import PARSER_VERSION
from .acquire import AcquisitionError, Fetcher, digest, identity, official_url
from .parse import parse_html

CSS = """
*,*::before,*::after{box-sizing:border-box}
body{overflow-wrap:anywhere;margin:0;background:#f3f4f6;color:#172033;font:18px/1.7 Georgia,serif}
header,main{width:100%;max-width:960px;margin:auto;padding:24px}header{font:15px/1.6 system-ui}
main{background:white;min-width:0}article,section,figure{min-width:0;max-width:100%}h1,h2,h3{line-height:1.25}figure{margin:28px 0}img{max-width:100%;height:auto}
table{overflow-wrap:normal;word-break:normal;border-collapse:collapse;display:block;overflow-x:auto;max-width:100%;font-size:15px}
td,th{padding:6px 12px;border:1px solid #ccd2db}figcaption,caption{font-size:16px;text-align:left}
math[display=block]{max-width:100%;overflow-wrap:normal;display:block;overflow-x:auto;margin:20px 0}a{color:#175ab0}
.missing{display:block;background:#fff0df;border:1px solid #b96013;padding:12px}
pre{overflow:auto} :target{outline:2px solid #d49e26} @media(max-width:600px){main{padding:16px}body{font-size:17px}}
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def preview(document, report):
    source = document["source"]
    source_link = ("Synthetic fixture · mock HTTP only" if report.get("fixture_only") else
                   f'<a href="{escape(source["resolved_url"], quote=True)}" rel="noreferrer noopener">Official source</a>')
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-src 'none'">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(document['title'])} — English preview</title><style>{CSS}</style></head>
<body><header><strong>English source preview · {escape(source['arxiv_id_version'])}</strong>
<p>Acquisition: {escape(report['status'])}. Registered PDF match: not checked. No translation.</p>
{source_link}
 · <a href="acquisition_report.json">Acquisition report</a></header><main>{document['html']}</main></body></html>'''


def image_extension(resource):
    allowed = {"image/png": (".png", b"\x89PNG\r\n\x1a\n"),
               "image/jpeg": (".jpg", b"\xff\xd8\xff"),
               "image/gif": (".gif", b"GIF8"),
               "image/webp": (".webp", b"RIFF")}
    extension, signature = allowed.get(resource.content_type, (None, None))
    if not signature or not resource.content.startswith(signature):
        raise AcquisitionError("unsupported_asset", "Only decoded PNG/JPEG/GIF/WebP images are enabled; SVG is not embedded")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(resource.content)) as image:
                if image.width * image.height > 50_000_000:
                    raise AcquisitionError("size_limit", "Image dimensions exceed limit")
                image.verify()
            with Image.open(BytesIO(resource.content)) as image:
                image.load()
    except AcquisitionError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise AcquisitionError("invalid_image", "Image could not be decoded") from error
    return extension


def snapshot(value, output_root, *, offline=False, fetcher=None, registered_pdf_identity=None):
    """registered_pdf_identity is an explicit future boundary, never PDF/body inference.

    Caller may supply an already user-verified versioned ID; no PDF/DB is opened here.
    """
    pinned = identity(value)
    if registered_pdf_identity and identity(registered_pdf_identity) != pinned:
        raise AcquisitionError("version_mismatch", "Explicit registered PDF identity differs")
    output = Path(output_root) / pinned.replace("/", "_")
    output.mkdir(parents=True, exist_ok=True)
    (output / "assets").mkdir(exist_ok=True)
    report = {"task": "RDR-01", "parser_version": PARSER_VERSION, "requested_input": value,
              "requested_url": f"https://arxiv.org/html/{pinned}", "arxiv_id_version": pinned,
              "representation": "arxiv_html", "run_at": now(), "offline": offline,
              "ai_calls": 0, "database_opened": False, "pdf_comparison": "not_checked",
              "registered_pdf_identity": registered_pdf_identity, "registered_pdf_match": "not_checked",
              "assets": [], "unverified": ["Same-version registered PDF content and figure/table comparison",
                                            "Source HTML conversion completeness", "MMAD real data", "Windows local execution"]}
    owned_fetcher = fetcher is None
    fetcher = fetcher or Fetcher()
    cache_file = output / "snapshot_manifest.json"
    source_file = output / "source.html.txt"  # inert raw snapshot, never inserted into preview
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else None
        if cache:
            raw = source_file.read_bytes()
            if cache.get("arxiv_id_version") != pinned or digest(raw) != cache.get("sha256"):
                raise AcquisitionError("cache_invalid", "Source identity/hash mismatch; use a fresh output root")
            resolved, acquired_at = cache["resolved_url"], cache["acquired_at"]
            official_url(resolved)
            report["cache_reused"] = True
        else:
            if offline:
                raise AcquisitionError("cache_missing", "No snapshot available offline")
            resource = fetcher.fetch(report["requested_url"])
            if resource.content_type not in ("text/html", "application/xhtml+xml"):
                raise AcquisitionError("not_full_html", "Response is not HTML")
            raw, resolved, acquired_at = resource.content, resource.resolved_url, now()
            cache = {"arxiv_id_version": pinned, "requested_url": report["requested_url"],
                     "resolved_url": resolved, "acquired_at": acquired_at, "sha256": digest(raw), "assets": []}
            report["cache_reused"] = False

        cached_assets = {a["requested_url"]: a for a in cache.get("assets", [])}
        loaded = []
        stop_requests = False

        def load_asset(url):
            nonlocal stop_requests
            item = {"requested_url": url, "status": "missing"}
            try:
                official_url(url)
                if len(loaded) >= 200:
                    raise AcquisitionError("asset_limit", "At most 200 distinct images per paper")
                old = cached_assets.get(url)
                if old and old.get("status") == "ok":
                    # Cache paths are generated, not remote filenames or untrusted paths.
                    expected = "assets/" + old.get("sha256", "") + old.get("extension", "")
                    if old.get("path") != expected or not re.fullmatch(r"[0-9a-f]{64}", old.get("sha256", "")) or old.get("extension") not in (".png", ".jpg", ".gif", ".webp"):
                        raise AcquisitionError("cache_invalid", "Invalid asset cache path")
                    local = output / expected
                    if local.is_file() and digest(local.read_bytes()) == old["sha256"]:
                        item = dict(old, cache_reused=True)
                        loaded.append(item)
                        return item
                if offline or stop_requests:
                    raise AcquisitionError("offline_missing" if offline else "requests_stopped", "Asset unavailable; no network request")
                resource = fetcher.fetch(url, asset=True)
                extension = image_extension(resource)
                sha = digest(resource.content)
                path = f"assets/{sha}{extension}"
                (output / path).write_bytes(resource.content)
                item.update(status="ok", resolved_url=resource.resolved_url, path=path, extension=extension,
                            sha256=sha, content_type=resource.content_type, acquired_at=now(), cache_reused=False)
            except (AcquisitionError, ValueError) as error:
                status = error.status if isinstance(error, AcquisitionError) else "unsafe_url"
                if status == "html_unavailable":
                    status = "asset_missing"
                if status in {"forbidden", "rate_limited", "robots_denied", "robots_unavailable"}:
                    stop_requests = True
                item.update(status=status, detail=str(error), fallback="Inspect corresponding figure in the original local PDF; automatic PDF integration is deferred")
            loaded.append(item)
            return item

        # Preserve acquisition provenance even when parsing/version checks fail.
        report.update(resolved_url=resolved, acquired_at=acquired_at, sha256=digest(raw))
        document, diagnostics = parse_html(raw, pinned, resolved, load_asset)
        document["source"].update(requested_url=report["requested_url"], acquired_at=acquired_at,
                                   registered_pdf_identity=registered_pdf_identity)
        report.update(diagnostics)
        report.update(resolved_url=resolved, acquired_at=acquired_at, sha256=digest(raw),
                      version_status="same_version_html_url", version_evidence=document["source"]["version_evidence"],
                      assets=loaded, asset_counts={"total": len(loaded), "ok": sum(a["status"] == "ok" for a in loaded),
                                                  "unresolved": sum(a["status"] != "ok" for a in loaded)})
        issues = (report["asset_counts"]["unresolved"] or diagnostics["warnings"] or diagnostics["missing_references"]
                  or diagnostics["duplicate_dom_ids"] or diagnostics["empty_regions"] or diagnostics["removed_markup"])
        report["status"] = "conversion_incomplete" if issues else "acquired"
        cache["assets"] = loaded
        source_file.write_bytes(raw)
        write_json(cache_file, cache)
        write_json(output / "reading_document.json", document)
        (output / "english_preview.html").write_text(preview(document, report), encoding="utf-8")
    except (AcquisitionError, OSError, ValueError, KeyError) as error:
        report.update(status=error.status if isinstance(error, AcquisitionError) else "snapshot_error", detail=str(error))
        # Never leave a stale success document beside a failed acquisition report.
        for name in ("reading_document.json", "english_preview.html"):
            (output / name).unlink(missing_ok=True)
    finally:
        report["network_events"] = fetcher.events
        write_json(output / "acquisition_report.json", report)
        if owned_fetcher:
            fetcher.close()
    return output, report
