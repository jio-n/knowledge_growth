"""Generate synthetic PDFs for local/browser checks; pytest generates them in memory."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.fixtures.pdf_factory import FIXTURE_NAMES, make_pdf  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "tests/fixtures/generated")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name in FIXTURE_NAMES:
        path = args.output_dir / f"{name}.pdf"
        path.write_bytes(make_pdf(name))
        print(path)


if __name__ == "__main__":
    main()
