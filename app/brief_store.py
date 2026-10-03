"""Dedicated field persistence. Caller owns the write transaction."""
import json
from .db import now
from .paper_brief import SCHEMA_VERSION


def get_brief(con, source_id):
    row = con.execute('SELECT * FROM paper_briefs WHERE source_id=?', (source_id,)).fetchone()
    if not row:
        return None
    fields = {}
    for field in con.execute('SELECT * FROM paper_brief_fields WHERE source_id=? ORDER BY field_name', (source_id,)):
        saved_evidence = json.loads(field['evidence_json'])
        evidence = saved_evidence['resolutions']
        fields[field['field_name']] = {
            'value': json.loads(field['value_json']), 'status': field['status'],
            'evidence': saved_evidence['refs'], 'evidence_resolution': evidence,
            'origin': field['origin'], 'user_edited': bool(field['user_edited']),
            'provenance': json.loads(field['provenance_json']),
            'verification': field['verification'], 'updated_at': field['updated_at']}
    return {**dict(row), 'fields': fields}


def write_fields(con, source_id, fields, *, automatic=True):
    ts = now()
    con.execute('''INSERT INTO paper_briefs (source_id,schema_version,created_at,updated_at)
        VALUES (?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET revision=revision+1,updated_at=excluded.updated_at''',
        (source_id, SCHEMA_VERSION, ts, ts))
    written = []
    for name, field in fields.items():
        protected = "WHERE paper_brief_fields.origin!='user' AND paper_brief_fields.user_edited=0" if automatic else ''
        result = con.execute('''INSERT INTO paper_brief_fields
            (source_id,field_name,value_json,status,evidence_json,origin,user_edited,provenance_json,verification,updated_at)
            VALUES (?,?,?,?,?,?,?,?,'unverified',?) ON CONFLICT(source_id,field_name) DO UPDATE SET
            value_json=excluded.value_json,status=excluded.status,evidence_json=excluded.evidence_json,
            origin=excluded.origin,user_edited=excluded.user_edited,provenance_json=excluded.provenance_json,
            verification='unverified',updated_at=excluded.updated_at ''' + protected,
            (source_id, name, json.dumps(field['value'], ensure_ascii=False, allow_nan=False), field['status'],
             json.dumps({'refs': field['evidence'], 'resolutions': field['evidence_resolution']}, ensure_ascii=False), field['origin'], int(field['user_edited']),
             json.dumps(field['provenance'], ensure_ascii=False), ts))
        if result.rowcount:
            written.append(name)
    return written
