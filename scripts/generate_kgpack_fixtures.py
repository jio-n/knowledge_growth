"""Write generated kgpack fixtures; no real research material or API calls."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fixtures.kgpack_factory import FIXTURE_NAMES, make_kgpack, package_data
from tests.fixtures.pdf_factory import make_pdf
from app.ingest.common import sha256_bytes
from app.import_bridge.validator import canonical
from app.import_bridge.schema import Package
from app.paper_brief import PaperBrief

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=Path('/tmp/kgpack-fixtures'))
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
pdf = make_pdf('visual_evidence')
(args.output / 'generated.pdf').write_bytes(pdf)
for name in FIXTURE_NAMES:
    (args.output / (name + '.kgpack')).write_bytes(make_kgpack(name, source_hash=sha256_bytes(pdf)))
(args.output / 'package.template.json').write_text(canonical(package_data()), encoding='utf-8')
(args.output / 'kgpack.schema.json').write_text(canonical(Package.model_json_schema()), encoding='utf-8')
(args.output / 'paper_brief.schema.json').write_text(canonical(PaperBrief.model_json_schema()), encoding='utf-8')
print(f'Generated {len(FIXTURE_NAMES)} packages, one PDF, a template and JSON schemas in {args.output}')
