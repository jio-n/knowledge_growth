"""PDF/API-key-free CLI validator and generator."""
import argparse
import json
from pathlib import Path
import sys
from pydantic import ValidationError
from .schema import Package
from .validator import MAX_PACKAGE_BYTES, PackageError, _constant, _object, generate_package, payload_hash, validate_package


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    validate = commands.add_parser('validate')
    validate.add_argument('package', type=Path)
    generate = commands.add_parser('generate', help='Build a kgpack from a combined Package JSON')
    generate.add_argument('json_file', type=Path)
    generate.add_argument('output', type=Path)
    args = parser.parse_args()
    try:
        path = args.package if args.command == 'validate' else args.json_file
        if path.stat().st_size > MAX_PACKAGE_BYTES:
            raise PackageError('oversized input')
        if args.command == 'validate':
            with path.open('rb') as stream:
                package = validate_package(stream.read(MAX_PACKAGE_BYTES + 1))
        else:
            package = Package.model_validate(json.loads(path.read_text(encoding='utf-8'),
                                                       object_pairs_hook=_object, parse_constant=_constant))
            data = generate_package(package)
            args.output.write_bytes(data)
        print(json.dumps({'valid': True, 'package_id': package.manifest.package_id,
                          'payload_hash': payload_hash(package), 'schema_version': package.manifest.kgpack_schema_version}))
        return 0
    except (PackageError, ValidationError, OSError, ValueError) as exc:
        error = 'invalid package schema' if isinstance(exc, ValidationError) else str(exc)
        print(json.dumps({'valid': False, 'error': error}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
