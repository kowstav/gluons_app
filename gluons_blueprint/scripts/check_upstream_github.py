"""OPTIONAL live upstream metadata research. No upstream source is imported, executed, or approved.

Use: GITHUB_TOKEN=... python scripts/check_upstream_github.py [--limit 24]
The output is informational. A repo MUST NOT be declared license-audited from API metadata alone.
"""
import argparse, json, os, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT=Path(__file__).resolve().parents[1]

def get(repo):
    url='https://api.github.com/repos/'+repo
    hdr={'Accept':'application/vnd.github+json','User-Agent':'Gluons-Review-Tool/0.1','X-GitHub-Api-Version':'2022-11-28'}
    token=os.environ.get('GITHUB_TOKEN')
    if token:hdr['Authorization']='Bearer '+token
    request=Request(url,headers=hdr)
    with urlopen(request,timeout=10) as res:
        data=json.load(res)
    return {'slug':repo,'archived':data.get('archived'),'pushed_at':data.get('pushed_at'),
            'default_branch':data.get('default_branch'),
            'api_reported_license':(data.get('license') or {}).get('spdx_id'),
            'stars':data.get('stargazers_count'),'url':data.get('html_url')}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--limit',type=int,default=24)
    args=parser.parse_args()
    regs=json.loads((ROOT/'repos/repository_registry.json').read_text())
    for r in regs[:max(0,args.limit)]:
        slug=r['url'].split('github.com/',1)[-1]
        try:
            print(json.dumps(get(slug),ensure_ascii=False))
        except (HTTPError,URLError,TimeoutError,ValueError) as exc:
            print(json.dumps({'slug':slug,'error':type(exc).__name__}))
        time.sleep(0.15)
