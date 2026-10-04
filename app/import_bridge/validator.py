"""Bounded in-memory ZIP validation. Never extract an untrusted member to disk."""
from __future__ import annotations

import hashlib
import io
import json
import stat
import struct
import zlib
import zipfile
from pydantic import ValidationError
from .schema import ALLOWED_PAYLOADS, REQUIRED_PAYLOADS, Package

MAX_PACKAGE_BYTES = 8 * 1024 * 1024
MAX_MEMBER_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 6 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200


class PackageError(ValueError):
    pass


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def payload_hash(package: Package) -> str:
    # Timestamp/package ID changes cannot bypass content deduplication.
    payload = package.model_dump(mode='json')
    for key in ('package_id', 'generated_at'):
        payload['manifest'].pop(key)
    # Additive provenance defaults must not change hashes of packages already
    # accepted before Structured Brief generation (paper-brief-0.1 is unchanged).
    def strip_new_defaults(value):
        if isinstance(value, dict):
            provenance = value.get('provenance')
            if isinstance(provenance, dict):
                for key in ('runtime', 'source_version', 'schema_version'):
                    if provenance.get(key) is None:
                        provenance.pop(key, None)
            for child in value.values():
                strip_new_defaults(child)
        elif isinstance(value, list):
            for child in value:
                strip_new_defaults(child)
    strip_new_defaults(payload)
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def _object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise PackageError('duplicate JSON key')
        obj[key] = value
    return obj


def _constant(value):
    raise PackageError('non-finite JSON number')


def validate_package(data: bytes) -> Package:
    if len(data) > MAX_PACKAGE_BYTES:
        raise PackageError('oversized package')
    if not data.startswith(b'PK\x03\x04') or data[-22:-18] != b'PK\x05\x06':
        raise PackageError('plain ZIP required; prepended/trailing data and comments are refused')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            names = [m.filename for m in members]
            if len(names) != len(set(names)):
                raise PackageError('duplicate ZIP member')
            if set(names) - ALLOWED_PAYLOADS:
                raise PackageError('unsafe/unexpected payload; PDF and assets are refused')
            if not REQUIRED_PAYLOADS <= set(names):
                raise PackageError('missing required payload')
            if sum(m.file_size for m in members) > MAX_TOTAL_BYTES:
                raise PackageError('oversized total payload')
            # Reject orphan local records/gaps that could conceal an unlisted PDF.
            cursor = 0
            ordered = sorted(members, key=lambda m: m.header_offset)
            for idx, member in enumerate(ordered):
                if member.header_offset != cursor or member.orig_filename != member.filename:
                    raise PackageError('unsafe ZIP layout/name')
                header = struct.unpack_from('<4s5H3I2H', data, cursor)
                if header[0] != b'PK\x03\x04':
                    raise PackageError('invalid local ZIP header')
                cursor += 30 + header[-2] + header[-1] + member.compress_size
                boundary = ordered[idx + 1].header_offset if idx + 1 < len(ordered) else archive.start_dir
                if member.flag_bits & 8:
                    size = boundary - cursor
                    if size not in (12, 16):
                        raise PackageError('invalid ZIP data descriptor')
                    if size == 16:
                        if data[cursor:cursor + 4] != b'PK\x07\x08':
                            raise PackageError('invalid ZIP data descriptor')
                        cursor += 4
                    if struct.unpack_from('<3I', data, cursor) != (member.CRC, member.compress_size, member.file_size):
                        raise PackageError('invalid ZIP data descriptor')
                    cursor += 12
                if cursor != boundary:
                    raise PackageError('unlisted ZIP data refused')
            payload = {}
            for member in members:
                mode = member.external_attr >> 16
                if (member.flag_bits & 1 or stat.S_ISLNK(mode) or
                    (stat.S_IFMT(mode) and not stat.S_ISREG(mode)) or
                    member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise PackageError('unsupported/encrypted/non-regular ZIP member')
                if (member.file_size > MAX_MEMBER_BYTES or
                    member.file_size > max(1, member.compress_size) * MAX_COMPRESSION_RATIO):
                    raise PackageError('oversized/compression-ratio payload')
                # ZIP readers truncate to the advertised size. Verify the actual
                # compressed stream too, so a valid JSON prefix cannot hide more data.
                header = struct.unpack_from('<4s5H3I2H', data, member.header_offset)
                start = member.header_offset + 30 + header[-2] + header[-1]
                compressed = data[start:start + member.compress_size]
                if member.compress_type == zipfile.ZIP_DEFLATED:
                    decoder = zlib.decompressobj(-15)
                    actual = decoder.decompress(compressed, MAX_MEMBER_BYTES + 1)
                    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                        raise PackageError('invalid/oversized compressed stream')
                else:
                    actual = compressed
                if len(actual) != member.file_size:
                    raise PackageError('actual ZIP payload size mismatch')
                with archive.open(member) as stream:
                    raw = stream.read(MAX_MEMBER_BYTES + 1)
                if len(raw) > MAX_MEMBER_BYTES:
                    raise PackageError('oversized payload')
                payload[member.filename] = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                                                     parse_constant=_constant)
            if set(payload['manifest.json']['payloads']) != set(names) - {'manifest.json'}:
                raise PackageError('payload list mismatch')
            return Package.model_validate({name.removesuffix('.json'): value for name, value in payload.items()})
    except (ValueError, TypeError, KeyError, zipfile.BadZipFile, RuntimeError, NotImplementedError,
            OSError, RecursionError, EOFError, struct.error, zlib.error) as exc:
        if isinstance(exc, PackageError):
            raise
        if isinstance(exc, ValidationError):
            # No input values in errors (do not echo imported content into logs).
            issues = [{'loc': e['loc'], 'type': e['type'], 'msg': e['msg']}
                      for e in exc.errors(include_input=False, include_url=False, include_context=False)]
            raise PackageError(canonical(issues)) from exc
        raise PackageError('malformed ZIP/JSON/package structure') from exc


def generate_package(package: Package) -> bytes:
    """Generate the exact same contract used by CLI, templates and API."""
    package = Package.model_validate(package.model_dump(mode='json'))
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in package.model_dump(mode='json').items():
            if value is not None:
                archive.writestr(name + '.json', canonical(value))
    data = out.getvalue()
    validate_package(data)
    return data
