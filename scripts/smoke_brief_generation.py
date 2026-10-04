"""Optional local ChatGPT-plan smoke; never run in CI, never log paper/output/auth.

Start local knowledge_growth, connect ChatGPT in the header, then pass --manual.
This registers an entirely generated PDF, generates/commits its Brief, checks local
Evidence anchors, and deletes only the synthetic source created by this script.
"""
import argparse
from pathlib import Path
import sys
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests.fixtures.brief_runtime import paper_pdf


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manual',action='store_true')
    parser.add_argument('--base-url',default='http://127.0.0.1:8300')
    args=parser.parse_args()
    if not args.manual:
        parser.error('--manual is required; this consumes ChatGPT-plan turns and is excluded from CI')
    with httpx.Client(base_url=args.base_url,timeout=20) as client:
        status=client.get('/api/ai/status').json()
        if status.get('runtime')!='codex_chatgpt_plan' or status.get('state')!='ready':
            print('AI未接続: headerの「ChatGPTで接続」で接続してください。')
            return 1
        sid=None
        try:
            uploaded=client.post('/api/sources/pdf',files={'file':('kg-generated-manual-smoke.pdf',paper_pdf(),'application/pdf')},data={'force':'true'})
            if not uploaded.is_success: raise RuntimeError('upload_failed')
            sid=uploaded.json()['source']['id']
            res=client.post(f'/api/sources/{sid}/paper-brief/generate')
            if res.status_code!=202: raise RuntimeError('start_failed')
            deadline=time.monotonic()+600
            while time.monotonic()<deadline:
                job=client.get(f'/api/sources/{sid}/paper-brief/generation').json()
                if job.get('state') in ('failed','preview'): break
                time.sleep(1)
            else: raise RuntimeError('generation_timeout')
            if job['state']!='preview': raise RuntimeError('generation_failed')
            res=client.post(f'/api/sources/{sid}/paper-brief/generation/commit',json={'generation_id':job['generation_id'],'confirmed':True})
            if not res.is_success: raise RuntimeError('commit_failed')
            fields=res.json()['paper_brief']['fields']
            blocks=client.get(f'/api/sources/{sid}/document').json()['blocks']
            ids={b['id'] for b in blocks}
            evidence=[e for f in fields.values() for e in f['evidence_resolution'] if e['status']=='resolved']
            assert evidence and all(e['anchor']['blockId'] in ids for e in evidence)
            print(f'PASS: shared Brief persisted; {len(fields)} fields; local Evidence verified. No paper/output/auth logged.')
            return 0
        except Exception:
            print('FAIL: generation/validation/persistence smoke. See safe generation state in Reader; no payload logged.')
            return 1
        finally:
            if sid: client.delete(f'/api/sources/{sid}')


if __name__=='__main__': raise SystemExit(main())
