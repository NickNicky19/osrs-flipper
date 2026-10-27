"""Deterministic one-portfolio interval replay under explicit, unverified fill rules.

An interval average crossing a preissued price is only a simulation trigger.
No output from this module is an observed user fill or validated execution edge.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_FLOOR
import argparse
import json
import math
from pathlib import Path

VERSION='interval-cross-participation-1'
MAX_PLANS=500
MAX_BARS=12_000
MAX_SPAN=31*86400


def _integer(value, positive=False):
    return type(value) is int and value >= (1 if positive else 0)


def _price(value):
    return type(value) in (int,float) and math.isfinite(value) and value>0


def _mark_gp(quantity,average):
    return int((Decimal(quantity)*Decimal(str(average))).to_integral_value(rounding=ROUND_FLOOR))


def _tax(sell, exempt):
    return 0 if exempt else min(sell//50,5_000_000)


def replay(plans, bars, *, capital=500_000_000, max_slots=4,
           publication_delay=300, action_delay=300, collection_delay=300,
           participation_bps=1000, max_position_gp=125_000_000,
           allow_expired_no_action=False):
    assumptions=dict(version=VERSION,capital=capital,max_slots=max_slots,
        publication_delay_seconds=publication_delay,action_delay_seconds=action_delay,
        collection_delay_seconds=collection_delay,participation_bps=participation_bps,
        max_position_gp=max_position_gp,allow_expired_no_action=allow_expired_no_action,
        trigger='Preplaced order receives up to the stated fraction of whole-bucket reported units only when the relevant bucket average crosses its limit. This is a hypothetical rule, not an observed fill or queue estimate.',
        allowance='Per-item public four-hour rolling window from first simulated purchase. ASSUMED_ITEM_ONLY is a sensitivity case and cannot satisfy a promotion-grade shared-limit gate.')
    if (not isinstance(plans,list) or not isinstance(bars,list) or len(plans)>MAX_PLANS or len(bars)>MAX_BARS or
            not _integer(capital,True) or not _integer(max_slots,True) or max_slots>4 or
            not all(_integer(x) and x<=7200 for x in (publication_delay,action_delay,collection_delay)) or
            not _integer(participation_bps) or participation_bps>1000 or
            not _integer(max_position_gp,True) or max_position_gp>125_000_000 or
            type(allow_expired_no_action) is not bool):
        return dict(status='DATA_INSUFFICIENT',reason='INVALID_OR_OVERSIZED_REPLAY_INPUT',assumptions=assumptions)
    if not plans or not bars:
        return dict(status='DATA_INSUFFICIENT',reason='NO_MATCHED_PLANS_AND_BARS',assumptions=assumptions)
    if any(not isinstance(b,dict) or
           not all(_integer(b.get(k),True) for k in ('item_id','bucket_start','received_at'))
           for b in bars):
        return dict(status='DATA_INSUFFICIENT',reason='INVALID_INTERVAL_ENVELOPE',assumptions=assumptions)
    if max(b.get('bucket_start',0) for b in bars)-min(b.get('bucket_start',0) for b in bars)>MAX_SPAN:
        return dict(status='DATA_INSUFFICIENT',reason='REPLAY_SPAN_LIMIT',assumptions=assumptions)
    ids=set();positions=[]
    for index,p in enumerate(plans):
        required=('id','item_id','issued_at','entry_deadline','exit_deadline','buy','sell','quantity','public_limit','tax_exempt')
        if (not isinstance(p,dict) or any(k not in p for k in required) or p['id'] in ids or
                not all(_integer(p[k],True) for k in ('item_id','issued_at','entry_deadline','exit_deadline','buy','sell','quantity','public_limit')) or
                type(p['tax_exempt']) is not bool or p['entry_deadline']<=p['issued_at'] or
                p['exit_deadline']<p['entry_deadline'] or
                ('activation_at' in p and (not _integer(p['activation_at'],True) or p['activation_at']<p['issued_at'])) or
                p.get('limit_group_status','VERIFIED_ITEM_ONLY') not in ('VERIFIED_ITEM_ONLY','ASSUMED_ITEM_ONLY')):
            return dict(status='DATA_INSUFFICIENT',reason='UNVERIFIED_PLAN_OR_ALLOWANCE',assumptions=assumptions)
        ids.add(p['id'])
        positions.append(dict(plan=p,index=index,activation=p.get('activation_at',p['issued_at']+publication_delay+action_delay),
            status='PENDING',reserved_gp=0,entry_remaining=p['quantity'],entry_limit=p['quantity'],
            held=0,held_cost=0,last_entry_bucket=None,bought=0,sold=0,realized=0,
            entry_buckets=0,exit_buckets=0))
    first_bars={}
    for b in sorted(bars,key=lambda row:(row.get('received_at',0),row.get('bucket_start',0))):
        if (not isinstance(b,dict) or not all(_integer(b.get(k),True) for k in ('item_id','bucket_start','received_at')) or
                b['received_at']<b['bucket_start']+300):
            continue
        key=(b['item_id'],b['bucket_start'])
        first_bars.setdefault(key,b)
    for pos in positions:
        p=pos['plan']
        first_bucket=((pos['activation']+299)//300)*300
        if first_bucket+300>p['entry_deadline']:
            if allow_expired_no_action:
                pos['status']='EXPIRED_BEFORE_ACTION';pos['entry_remaining']=0
                continue
            return dict(status='DATA_INSUFFICIENT',reason='NO_COMPLETE_ENTRY_WINDOW_BEFORE_QUOTE_EXPIRY',assumptions=assumptions)
        expected=range(first_bucket,p['exit_deadline']-299,300)
        if not expected:
            return dict(status='DATA_INSUFFICIENT',reason='NO_COMPLETE_ACTION_WINDOW',assumptions=assumptions)
        for bucket in expected:
            observation=first_bars.get((p['item_id'],bucket))
            if (observation is None or not _price(observation.get('low_avg')) or
                    not _price(observation.get('high_avg')) or
                    not _integer(observation.get('low_units')) or
                    not _integer(observation.get('high_units'))):
                return dict(status='DATA_INSUFFICIENT',reason='INCOMPLETE_ITEM_ACTION_WINDOW',
                            item_id=p['item_id'],missing_bucket=bucket,assumptions=assumptions)
    ordered=sorted(first_bars.values(),key=lambda b:(b['bucket_start'],b['item_id']))
    if not ordered:return dict(status='DATA_INSUFFICIENT',reason='NO_COMPLETED_INTERVALS',assumptions=assumptions)
    cash=capital;claimable=[];allowance={};capacity_used=defaultdict(int);latest_mark={};equity=[]
    for bucket in sorted({b['bucket_start'] for b in ordered}):
        batch=[b for b in ordered if b['bucket_start']==bucket]
        due=[x for x in claimable if x[0]<=bucket]
        cash+=sum(amount for _,amount in due)
        claimable=[x for x in claimable if x[0]>bucket]
        for pos in positions:
            p=pos['plan']
            if pos['status']=='ACTIVE' and bucket+300>p['entry_deadline']:
                cash+=pos['reserved_gp'];pos['reserved_gp']=0
                pos['entry_remaining']=0
                pos['status']='OPEN_STOCK' if pos['held'] else 'EXPIRED'
        active_count=sum(pos['status'] in ('ACTIVE','OPEN_STOCK') for pos in positions)
        for pos in sorted(positions,key=lambda x:(x['plan']['issued_at'],x['index'])):
            p=pos['plan']
            if pos['status']!='PENDING' or bucket<pos['activation'] or bucket+300>p['entry_deadline']:
                continue
            if active_count>=max_slots:continue
            qty=min(p['quantity'],p['public_limit'],max_position_gp//p['buy'],cash//p['buy'])
            if qty<1:continue
            reserve=qty*p['buy'];cash-=reserve;pos['reserved_gp']=reserve
            pos['entry_limit']=qty;pos['entry_remaining']=qty;pos['status']='ACTIVE';active_count+=1
        # Existing sell offers get a separate later bucket. Claimable proceeds do not fund this bucket's buys.
        for b in batch:
            item=b['item_id']
            if _price(b.get('low_avg')):latest_mark[item]=(b['bucket_start'],b['low_avg'])
            for pos in positions:
                p=pos['plan']
                if (p['item_id']!=item or pos['held']<1 or pos['status'] not in ('ACTIVE','OPEN_STOCK') or
                        pos['last_entry_bucket'] is None or bucket<=pos['last_entry_bucket'] or bucket+300>p['exit_deadline'] or
                        not _price(b.get('high_avg')) or b['high_avg']<p['sell'] or
                        not _integer(b.get('high_units'))):
                    continue
                capacity=max(0,b['high_units']*participation_bps//10000-capacity_used[(item,bucket,'high')])
                qty=min(pos['held'],capacity)
                if qty<1:continue
                cost=pos['held_cost']*qty//pos['held']
                pos['held']-=qty;pos['held_cost']-=cost;pos['sold']+=qty;pos['exit_buckets']+=1
                net=qty*(p['sell']-_tax(p['sell'],p['tax_exempt']))
                pos['realized']+=net-cost;claimable.append((bucket+collection_delay,net))
                capacity_used[(item,bucket,'high')]+=qty
                if pos['held']==0 and pos['entry_remaining']==0 and pos['reserved_gp']==0:pos['status']='CLOSED'
        for b in batch:
            item=b['item_id']
            for pos in positions:
                p=pos['plan']
                if (p['item_id']!=item or pos['status']!='ACTIVE' or pos['entry_remaining']<1 or bucket+300>p['entry_deadline'] or
                        not _price(b.get('low_avg')) or b['low_avg']>p['buy'] or
                        not _integer(b.get('low_units'))):
                    continue
                window=allowance.get(item)
                if window is None or bucket>=window[0]+4*3600:window=[bucket,0];allowance[item]=window
                capacity=max(0,b['low_units']*participation_bps//10000-capacity_used[(item,bucket,'low')])
                qty=min(pos['entry_remaining'],capacity,p['public_limit']-window[1],pos['reserved_gp']//p['buy'])
                if qty<1:continue
                spent=qty*p['buy'];pos['reserved_gp']-=spent;pos['entry_remaining']-=qty
                pos['held']+=qty;pos['held_cost']+=spent;pos['bought']+=qty;pos['entry_buckets']+=1
                pos['last_entry_bucket']=bucket;window[1]+=qty;capacity_used[(item,bucket,'low')]+=qty
                if pos['entry_remaining']==0:
                    cash+=pos['reserved_gp'];pos['reserved_gp']=0
        free=cash+sum(p['reserved_gp'] for p in positions)+sum(x[1] for x in claimable)
        marks=[]
        for pos in positions:
            if pos['held']:
                mark=latest_mark.get(pos['plan']['item_id'])
                if mark is None or bucket-mark[0]>900:marks=None;break
                marks.append(_mark_gp(pos['held'],mark[1]))
        if marks is not None:equity.append((bucket,free+sum(marks)))
    terminal=ordered[-1]['bucket_start']+300
    cash+=sum(amount for due,amount in claimable if due<=terminal)
    claimable=[x for x in claimable if x[0]>terminal]
    for pos in positions:
        if pos['reserved_gp']:
            cash+=pos['reserved_gp'];pos['reserved_gp']=0
        if pos['status']=='EXPIRED_BEFORE_ACTION':pass
        elif pos['status']=='PENDING':pos['status']='NO_SLOT_OR_CASH'
        elif pos['held']:pos['status']='OPEN_STOCK'
        elif pos['bought']==0:pos['status']='ZERO_FILL'
        else:pos['status']='CLOSED'
    held_cost=sum(p['held_cost'] for p in positions)
    realized=sum(p['realized'] for p in positions)
    claim=sum(x[1] for x in claimable)
    if cash+claim+held_cost!=capital+realized:
        raise AssertionError('Simulated cash/stock conservation failed')
    peak=capital;drawdown=0
    for _,value in equity:
        peak=max(peak,value);drawdown=max(drawdown,(peak-value)*10000//peak)
    missing_mark=any(p['held'] and (p['plan']['item_id'] not in latest_mark or
                      terminal-latest_mark[p['plan']['item_id']][0]>900) for p in positions)
    uncertain_mark=None if missing_mark else sum(_mark_gp(p['held'],latest_mark[p['plan']['item_id']][1]) for p in positions)
    return dict(status='HISTORICAL_SIMULATION',evidence_class='HISTORICAL_SIMULATION',
        assumptions=assumptions,allowance_groups_verified=all(p['plan'].get('limit_group_status','VERIFIED_ITEM_ONLY')=='VERIFIED_ITEM_ONLY' for p in positions),
        realized_after_tax_gp=realized,cash_gp=cash,claimable_gp=claim,
        modeled_bought_units=sum(p['bought'] for p in positions),
        modeled_sold_units=sum(p['sold'] for p in positions),
        expired_before_action_count=sum(p['status']=='EXPIRED_BEFORE_ACTION' for p in positions),
        open_stock_cost_gp=held_cost,uncertain_open_stock_reference_mark_gp=uncertain_mark,
        uncertain_total_equity_gp=None if uncertain_mark is None else cash+claim+uncertain_mark,
        drawdown_bps=drawdown if len(equity)==len({b['bucket_start'] for b in ordered}) else None,
        complete_equity_clocks=len(equity),total_clocks=len({b['bucket_start'] for b in ordered}),
        positions=[dict(id=p['plan']['id'],item_id=p['plan']['item_id'],status=p['status'],
            modeled_bought=p['bought'],modeled_sold=p['sold'],remaining=p['held'],
            remaining_cost_gp=p['held_cost'],realized_after_tax_gp=p['realized']) for p in positions],
        warning=f'Interval-average contacts and {participation_bps/100:.0f}% of reported units are assumed fill rules. Neither actual fills nor a profit guarantee.')


def main():
    parser=argparse.ArgumentParser(description='Bounded interval replay under named assumptions')
    parser.add_argument('--input',required=True,help='JSON with plans and completed bars')
    parser.add_argument('--sensitivity',action='store_true',
                        help='Also run separate 2%, 5%, 10% reported-unit participation assumptions')
    args=parser.parse_args()
    body=json.loads(Path(args.input).read_text(encoding='utf-8'))
    if not isinstance(body,dict):
        raise SystemExit('Replay input must be a JSON object.')
    allowed={'capital','max_slots','publication_delay','action_delay',
             'collection_delay','participation_bps','max_position_gp'}
    assumptions=body.get('assumptions',{})
    if not isinstance(assumptions,dict) or set(assumptions)-allowed:
        raise SystemExit('Unknown replay assumption; no scenario was run.')
    plans,bars=body.get('plans',[]),body.get('bars',[])
    result=replay(plans,bars,**assumptions)
    if args.sensitivity:
        scoped={k:v for k,v in assumptions.items() if k!='participation_bps'}
        result={'default':result,'sensitivity':{
            label:replay(plans,bars,participation_bps=bps,**scoped)
            for label,bps in [('LOW_2_PERCENT',200),('MID_5_PERCENT',500),
                              ('BASELINE_10_PERCENT',1000)]},
            'note':'Separate hypothetical fill sensitivities. The 10% rule is the frozen baseline sizing assumption; none estimates queue depth or observed fills.'}
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
