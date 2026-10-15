"""Small PDF-only comparison, isolated from the application, DB, and AI.

Run only with generated/reusable PDFs in cloud; private papers stay on local machine.
Optional engines are deliberately not application requirements. Docling must have
pre-provisioned model artifacts; this runner does not initiate model downloads.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import platform
import time
from importlib.metadata import version
from pathlib import Path


def compare(paths, output, artifacts=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    report = {"selected_engine": None, "platform": platform.platform(), "ai_calls": 0,
              "windows_installation": "not_measured; optional engine wheels/models must be tested on Windows",
              "manual_checks": ["reading order", "omitted content", "figure/caption association", "table cells and values"],
              "runs": []}
    for engine in ("docling", "pymupdf4llm"):
        available = importlib.util.find_spec(engine) is not None
        for index, path in enumerate(paths):
            path = Path(path)
            run = {"engine": engine, "fixture": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                   "status": "not_installed", "quality": "not_verified", "elapsed_seconds": None}
            if available:
                started = time.perf_counter()
                try:
                    run["engine_version"] = version(engine)
                    if engine == "pymupdf4llm":
                        import pymupdf4llm
                        text = pymupdf4llm.to_markdown(str(path))
                    else:
                        if not artifacts or not Path(artifacts).is_dir():
                            run["status"] = "local_models_not_provisioned"
                            report["runs"].append(run)
                            continue
                        os.environ["HF_HUB_OFFLINE"] = "1"
                        os.environ["TRANSFORMERS_OFFLINE"] = "1"
                        from docling.document_converter import DocumentConverter, PdfFormatOption
                        from docling.datamodel.base_models import InputFormat
                        from docling.datamodel.pipeline_options import PdfPipelineOptions
                        options = PdfPipelineOptions(artifacts_path=Path(artifacts), do_ocr=False,
                                                     enable_remote_services=False)
                        converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
                        text = converter.convert(str(path)).document.export_to_markdown()
                    name = f"{index}-{engine}.md"
                    text = text.rstrip() + "\n"
                    (output / name).write_text(text, encoding="utf-8")
                    run.update(status="extracted", markdown=name, characters=len(text),
                               image_markers=text.count("!["), table_lines=sum("|" in line for line in text.splitlines()))
                except Exception as error:
                    run.update(status="failed", error=f"{type(error).__name__}: {error}")
                run["elapsed_seconds"] = round(time.perf_counter() - started, 4)
            report["runs"].append(run)
    (output / "comparison_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", nargs="+")
    parser.add_argument("--output", required=True)
    parser.add_argument("--docling-artifacts", help="Existing local model directory; never downloaded here")
    args = parser.parse_args()
    result = compare(args.pdf, args.output, args.docling_artifacts)
    print(json.dumps({"runs": [{k: v for k, v in r.items() if k in ("engine", "fixture", "status", "elapsed_seconds")}
                               for r in result["runs"]]}))
