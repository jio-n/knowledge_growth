"""Conservative, metadata-only source matching; no network or AI calls."""
import json
import re
import unicodedata
from .schema import SourceIdentity


def title_key(value):
    return re.sub(r'\W+', ' ', unicodedata.normalize('NFKC', value or '').casefold()).strip()


def doi_key(value):
    return re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', (value or '').strip(), flags=re.I).casefold()


def arxiv_key(value):
    value = re.sub(r'^(?:https?://arxiv\.org/(?:abs|pdf)/|arxiv:\s*)', '', (value or '').strip(), flags=re.I)
    return re.sub(r'v\d+$', '', value.removesuffix('.pdf'), flags=re.I).casefold()


def local_sources(con):
    return [dict(r) for r in con.execute('''SELECT s.id,s.title,s.authors,s.year,s.doi,s.arxiv_id,
        v.id AS version_id,v.content_hash FROM sources s JOIN source_versions v ON v.id=(
        SELECT id FROM source_versions WHERE source_id=s.id ORDER BY fetched_at DESC,rowid DESC LIMIT 1)
        WHERE s.type='pdf' ORDER BY s.id''')]


def match_source(con, identity: SourceIdentity) -> dict:
    sources = local_sources(con)
    authors = sorted(title_key(a) for a in identity.authors)
    tests = [
        ('content_hash', 'strong', lambda s: bool(identity.source_hash) and identity.source_hash.lower() == (s['content_hash'] or '').lower()),
        ('doi', 'strong', lambda s: bool(identity.doi) and doi_key(identity.doi) == doi_key(s['doi'])),
        ('arxiv_id', 'strong', lambda s: bool(identity.arxiv_id) and arxiv_key(identity.arxiv_id) == arxiv_key(s['arxiv_id'])),
        ('title_authors_year', 'weak', lambda s: bool(authors) and identity.year is not None and
            title_key(identity.title) == title_key(s['title']) and identity.year == s['year'] and
            authors == sorted(title_key(a) for a in json.loads(s['authors'] or '[]'))),
        ('title', 'weak', lambda s: title_key(identity.title) == title_key(s['title'])),
    ]
    for method, strength, test in tests:
        hits = [s for s in sources if test(s)]
        if not hits:
            continue
        contradictions = []
        for s in hits:
            for key, normalizer in [('doi', doi_key), ('arxiv_id', arxiv_key)]:
                if getattr(identity, key) and s[key] and normalizer(getattr(identity, key)) != normalizer(s[key]):
                    contradictions.append({'source_id': s['id'], 'field': key})
        strong = strength == 'strong' and len(hits) == 1 and not contradictions
        return {'status': 'matched' if len(hits) == 1 else 'ambiguous', 'method': method,
                'strength': strength, 'candidates': hits, 'contradictions': contradictions,
                'source_id': hits[0]['id'] if strong else None,
                'manual_selection_required': not strong}
    return {'status': 'unmatched', 'method': None, 'strength': None, 'candidates': [],
            'contradictions': [], 'source_id': None, 'manual_selection_required': True}
