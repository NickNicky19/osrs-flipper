"""Private HTTP-facing research service. No HTTP handler can start collection."""
import time
import threading
from pathlib import Path
from core import ValidationError
from market import MarketStore,strict_json,sha
from research import build_report,explore,receipt,journal_evidence,inventory_valuation
from demo_research import seed_demo

class ResearchService:
    def __init__(self,ledger,root:Path,market_database=None,rules_path=None):
        self.ledger=ledger;self.root=Path(root);self.lock=threading.RLock();self.cache={}
        self.rules_path=Path(rules_path) if rules_path else self.root/'config'/'item-rules.json'
        self.demo_now=None
        if ledger.mode=='demo':
            self.store,self.demo_rules,self.demo_now=seed_demo(ledger.path.parent/'research-demo.sqlite3')
        else:
            self.store=MarketStore(market_database or ledger.path.parent/'market.sqlite3','public')

    def report(self,window=3600):
        rules=self.demo_rules if self.demo_now else strict_json(self.rules_path.read_bytes()) if self.rules_path.exists() else {}
        snapshot=self.store.snapshot(self.demo_now,window)
        report=build_report(snapshot,rules)
        state=self.ledger.get()
        report.pop('report_hash',None)
        report['account_state_hash']=sha(state)
        report['report_hash']=sha(report)
        with self.lock:
            self.cache[report['report_hash']]=(report,state)
            while len(self.cache)>20:self.cache.pop(next(iter(self.cache)))
        return report

    def context(self,key):
        if not isinstance(key,str) or len(key)!=64:
            raise ValidationError('A report hash from this session is required.')
        with self.lock:
            ctx=self.cache.get(key)
        if not ctx:raise ValidationError('Report is no longer in this session; refresh research.')
        report,state=ctx
        if sha(self.ledger.get())!=report['account_state_hash']:
            raise ValidationError('Recorded account state changed. Refresh before using this scenario.')
        if self.demo_now is None:
            elapsed=int(time.time())-report['as_of']
            if not 0<=elapsed<=60 or int(time.time())>=report['valid_until']:
                raise ValidationError('Research view expired. Refresh observations first.')
        return report,state

    def scenario(self,body):
        report,state=self.context(body.get('report_hash'))
        ident=body.get('item_id')
        if type(ident) is not int:raise ValidationError('Item ID must be an integer.')
        row=next((r for r in report['rows'] if r['item_id']==ident),None)
        if row is None:raise ValidationError('Item not present in this report.')
        if self.demo_now is None:
            if row['rule'] is not None and int(time.time())>=row['rule']['expires_at']:
                raise ValidationError('Item-rule review expired. Refresh and review rules.')
            elapsed=int(time.time())-report['as_of']
            if any(row[side+'_age'] is None or row[side+'_age']+elapsed>report['policy']['max_quote_age_seconds'] for side in ('low','high')):
                raise ValidationError('Quote aged beyond the policy; refresh.')
        return explore(row,body.get('buy'),body.get('sell'),body.get('quantity'),state)

    def freeze(self,body):
        report,state=self.context(body.get('report_hash'))
        scenario=self.scenario(body) if 'item_id' in body else None
        return self.store.freeze(body.get('id'),receipt(report,state,scenario))

    def validate_link(self,event):
        if event.get('type')!='place' or event.get('decision_id') is None:
            return
        decisions=self.store.decisions()
        saved=next((d for d in decisions if d['id']==event['decision_id']),None)
        if not saved:
            raise ValidationError('Saved research receipt not found in this namespace.')
        scenario=saved['receipt'].get('scenario')
        if not scenario or scenario['item_id']!=event.get('item_id'):
            raise ValidationError('Receipt must refer to the same item. Record without a link rather than misattribute an offer.')

    def evidence(self):
        e=journal_evidence(self.ledger.export())
        decisions=self.store.decisions()
        e['saved_research_receipts']=len(decisions)
        e['namespace']=self.store.namespace
        e['inventory_reference_scenario']=inventory_valuation(self.ledger.get(),self.report())
        e['linked_recorded_offers']=sum(bool(o.get('decision_id')) for o in self.ledger.get()['offers'].values())
        e['last_receipts']=[dict(id=d['id'],digest=d['digest'],created_at=d['created_at'],
            item_id=(d['receipt'].get('scenario') or {}).get('item_id'),
            item_name=(d['receipt'].get('scenario') or {}).get('name')) for d in decisions[-20:]]
        return e
