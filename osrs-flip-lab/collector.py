"""Explicit opt-in public market collector. No vendor or game calls.

--probe makes at most one request per supported endpoint and records a receipt.
--once/--watch require an unexpired local policy/semantics review. No scheduler
is installed. Ctrl+C stops collection; an offline host cannot keep collecting.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
from core import ValidationError
from market import MarketStore,SourceContract,MAX_RAW_BYTES,strict_json,KINDS

ROOT=Path(__file__).resolve().parent
HOST='prices.runescape.wiki'

class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise ValidationError('Unexpected redirect rejected; review the source contract.')


def fetch_public(kind,contract,timeout=15):
    if kind not in KINDS:
        raise ValidationError('Unknown route.')
    if not contract.user_agent.strip() or 'replace with' in contract.user_agent:
        raise ValidationError('Set an identifying project/contact User-Agent first.')
    url=f'https://{HOST}/api/{contract.version_for(kind)}/osrs/{kind}'
    request=urllib.request.Request(url,headers={'User-Agent':contract.user_agent,'Accept':'application/json','Accept-Encoding':'identity'})
    opener=urllib.request.build_opener(NoRedirects())
    try:
        with opener.open(request,timeout=timeout) as r:
            if urllib.parse.urlsplit(r.url).hostname!=HOST:
                raise ValidationError('Unexpected source host.')
            if r.status!=200 or r.headers.get_content_type() not in ('application/json','text/json'):
                raise ValidationError('Unexpected response status or content type.')
            raw=r.read(MAX_RAW_BYTES+1)
            if len(raw)>MAX_RAW_BYTES:
                raise ValidationError('Response too large.')
            return raw,dict(url=url,http_status=r.status,server_date=r.headers.get('Date'),
                            cache_control=r.headers.get('Cache-Control'),age=r.headers.get('Age'))
    except urllib.error.HTTPError as exc:
        retry=exc.headers.get('Retry-After') if exc.headers else None
        raise ValidationError(f'HTTP {exc.code}; Retry-After={retry}. No immediate retry; review access before restarting.') from exc


def collect_once(store,contract,kinds=('mapping','latest','5m'),probe=False,fetcher=fetch_public):
    now=int(time.time())
    if not probe and contract.blockers(now):
        raise ValidationError('Collection disabled: '+', '.join(contract.blockers(now)))
    results=[]
    for kind in kinds:
        try:
            raw,headers=fetcher(kind,contract)
            stamp=int(time.time())
            result=store.ingest(kind,raw,stamp,contract)
            result.update(kind=kind,received_at=stamp,headers=headers)
            results.append(result)
            if not result['ok']:break
        except Exception as exc:
            stamp=int(time.time());store.failure(kind,str(exc),stamp)
            results.append(dict(kind=kind,ok=False,received_at=stamp,error=str(exc)))
            # Do not hammer other endpoints when the provider or network rejects access.
            break
    return dict(attempted_at=now,probe=probe,namespace=store.namespace,results=results,
                status='SCHEMA_CAPTURED_REVIEW_REQUIRED' if probe and results and all(r['ok'] for r in results)
                       else 'CAPTURED' if results and all(r['ok'] for r in results) else 'BLOCKED',
                note='A schema pass does not verify permission, timestamps, whole-market coverage, or item rules.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--probe',action='store_true');mode.add_argument('--once',action='store_true');mode.add_argument('--watch',action='store_true')
    p.add_argument('--review',type=Path,required=True)
    p.add_argument('--database',type=Path,default=ROOT/'data'/'market.sqlite3')
    p.add_argument('--interval',type=int,default=60)
    p.add_argument('--receipt',type=Path,default=ROOT/'data'/'collector-receipt.json')
    args=p.parse_args()
    try:
        contract=SourceContract.from_dict(strict_json(args.review.read_bytes()))
        if args.interval<60:raise ValidationError('Minimum interval is 60 seconds; current provider terms may require slower collection.')
        store=MarketStore(args.database,'public')
        last_day,last_bucket=None,None
        consecutive_failures=0
        while True:
            now=int(time.time());day=now//86400;bucket=now//300
            kinds=['latest']
            if day!=last_day:kinds.insert(0,'mapping')
            if bucket!=last_bucket:kinds.append('5m')
            result=collect_once(store,contract,tuple(kinds),probe=args.probe)
            args.receipt.parent.mkdir(parents=True,exist_ok=True)
            args.receipt.write_text(json.dumps(result,indent=2))
            with args.receipt.with_suffix('.jsonl').open('a',encoding='utf-8') as history:
                history.write(json.dumps(result,sort_keys=True)+'\n')
            print(json.dumps(result,indent=2),flush=True)
            if result['status']=='BLOCKED':
                consecutive_failures+=1
                error=str(result['results'][-1].get('error','')) if result['results'] else ''
                transient=not any(term in error for term in ('HTTP 401','HTTP 403','Unknown','review','Invalid','Unsupported'))
                if not args.watch or not transient or consecutive_failures>=3:
                    print('Collector paused after source failure; inspect the durable receipt history.')
                    return 2
                time.sleep(min(300,args.interval*(2**(consecutive_failures-1))))
                continue
            consecutive_failures=0
            last_day,last_bucket=day,bucket
            if not args.watch:return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('Collector stopped. No scheduled job remains.');return 0
    except (OSError,ValueError,ValidationError) as exc:
        p.error(str(exc))

if __name__=='__main__':
    raise SystemExit(main())
