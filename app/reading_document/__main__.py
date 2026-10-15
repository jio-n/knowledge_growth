"""python -m app.reading_document 2410.09453v3 --output reading-output"""
import argparse
import json

from .acquire import AcquisitionError
from .snapshot import snapshot


def main():
    parser = argparse.ArgumentParser(description="RDR-01: one pinned arXiv HTML snapshot; no DB or AI")
    parser.add_argument("arxiv", help="Explicit arXiv IDvN or official abs/html/pdf URL")
    parser.add_argument("--output", required=True, help="Dedicated output root (outside app data/)")
    parser.add_argument("--offline", action="store_true", help="Rebuild from hash-verified local cache only")
    parser.add_argument("--registered-pdf-id", help="Optional explicitly verified version ID, never inferred from PDF text")
    args = parser.parse_args()
    try:
        output, report = snapshot(args.arxiv, args.output, offline=args.offline,
                                  registered_pdf_identity=args.registered_pdf_id)
    except AcquisitionError as error:
        print(json.dumps({"status": error.status, "detail": str(error)}))
        return 2
    print(json.dumps({"status": report["status"], "output": str(output), "assets": report.get("asset_counts")}, ensure_ascii=False))
    return 0 if report["status"] == "acquired" else 1


if __name__ == "__main__":
    raise SystemExit(main())
