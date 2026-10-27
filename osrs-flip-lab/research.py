"""Explainable scenario research, NOT an execution-probability model.

All rankings are explicitly named descriptive rankings. Counterfactual cash is
conditional on fills, not an expected return. No source/model access to OSRS.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_FLOOR
from core import ValidationError, integer, tax_per_unit, summary, MAX_INTEGER
from market import SourceContract, aggregate_item, sha

ENGINE_VERSION='scenario-engine-0.3.0'

@dataclass(frozen=True)
class ScreenPolicy:
    max_quote_age_seconds: int=300
    max_receipt_age_seconds: int=600
    min_coverage_bps: int=7500
    max_mapping_age_seconds: int=604800
    min_side_units: int=1

    def validate(self):
        for key,value in asdict(self).items():
            integer(value,key,1)
        if self.min_coverage_bps>10000:
            raise ValidationError('Coverage cannot exceed 100%.')
        return self


def rule_for(rules,ident,now):
    if not isinstance(rules,dict):
        raise ValidationError('Item-rule registry must be an object.')
    r=rules.get(str(ident))
    if not isinstance(r,dict):
        return None,'ITEM_RULE_NOT_REVIEWED'
    if type(r.get('tax_exempt')) is not bool or r.get('tax_regime')!='ordinary-2pct-cap-5m':
        return None,'ITEM_TAX_RULE_UNSUPPORTED'
    for key in ('effective_from','reviewed_at','expires_at'):
        if type(r.get(key)) is not int:
            return None,'ITEM_RULE_DATES_INVALID'
    if not (0<r['effective_from']<=now and 0<r['reviewed_at']<=now<r['expires_at']):
        return None,'ITEM_RULE_EXPIRED_OR_FUTURE'
    if not isinstance(r.get('reference'),str) or not r['reference'].strip():
        return None,'ITEM_RULE_REFERENCE_MISSING'
    return r,None


def break_even_price(buy: int,exempt: bool):
    integer(buy,'Purchase price',1)
    tax_per_unit(buy,exempt)
    lo,hi=buy,min(MAX_INTEGER,buy+5_000_000)
    if hi-tax_per_unit(hi,exempt)<buy:
        raise ValidationError('Breakeven price exceeds supported integer range.')
    while lo<hi:
        mid=(lo+hi)//2
        if mid-tax_per_unit(mid,exempt)>=buy:
            hi=mid
        else:
            lo=mid+1
    return lo


def unit_scenario(buy,sell,exempt):
    integer(buy,'Purchase price',1);integer(sell,'Sale price',1)
    tax=tax_per_unit(sell,exempt)
    net=sell-tax-buy
    be=break_even_price(buy,exempt)
    return dict(buy=buy,sell=sell,tax_per_unit=tax,net_per_unit=net,
                conditional_roi_bps=net*10000//buy,breakeven_sell=be,
                exit_price_cushion_bps=(sell-be)*10000//sell)


def price_grid(low,high,exempt):
    # Transparent research alternatives, not optimal prices or priority assumptions.
    gap=high-low
    if gap<=0:
        return []
    result=[];seen=set()
    for label,offset in [('quoted references',0),('10% inside the observed spread',max(1,gap//10)),
                         ('25% inside the observed spread',max(1,gap//4))]:
        b,s=low+offset,high-offset
        if (b,s) in seen or b>=s:
            continue
        seen.add((b,s))
        row=unit_scenario(b,s,exempt);row['label']=label;result.append(row)
    return result


def build_report(snapshot,rules,policy=ScreenPolicy()):
    policy.validate()
    now=snapshot['as_of'];namespace=snapshot['namespace']
    global_flags=[]
    caps=[snapshot['mapping'],snapshot['latest'],*snapshot['bars']]
    if snapshot['mapping'] is None:
        global_flags.append('NO_MAPPING_CAPTURE')
    if snapshot['latest'] is None:
        global_flags.append('NO_QUOTE_CAPTURE')
    if namespace=='public':
        for c in caps:
            if c:
                global_flags+=SourceContract.from_dict(c['contract']).blockers(now)
        for a in snapshot['attempts']:
            if not a['ok']:
                global_flags.append('LAST_'+a['kind'].upper()+'_FETCH_FAILED')
    if snapshot['latest'] and now-snapshot['latest']['confirmed_at']>policy.max_receipt_age_seconds:
        global_flags.append('QUOTE_CAPTURE_TOO_OLD')
    if snapshot['mapping'] and now-snapshot['mapping']['confirmed_at']>policy.max_mapping_age_seconds:
        global_flags.append('MAPPING_CAPTURE_TOO_OLD')
    global_flags=sorted(set(global_flags))
    mapping=snapshot['mapping']['data']['items'] if snapshot['mapping'] else {}
    quotes=snapshot['latest']['data']['items'] if snapshot['latest'] else {}
    rows=[]
    for ident in sorted(quotes,key=int):
        q=quotes[ident];meta=mapping.get(ident)
        flags=list(global_flags)
        if not meta:
            flags.append('ITEM_METADATA_MISSING')
        low,high=q['low'],q['high']
        ages={side:(now-q[side+'_time'] if q[side+'_time'] is not None else None) for side in ('low','high')}
        for side in ('low','high'):
            if ages[side] is None:
                flags.append(side.upper()+'_QUOTE_MISSING')
            elif not 0<=ages[side]<=policy.max_quote_age_seconds:
                flags.append(side.upper()+'_QUOTE_STALE')
        activity=aggregate_item(snapshot,ident)
        if activity['coverage_bps']<policy.min_coverage_bps:
            flags.append('INSUFFICIENT_INTERVAL_COVERAGE')
        if min(activity['low_units'],activity['high_units'])<policy.min_side_units:
            flags.append('INSUFFICIENT_TWO_SIDED_ACTIVITY')
        rule,rule_error=rule_for(rules,ident,now)
        if rule_error:
            flags.append(rule_error)
        scenarios=[]
        positive_bars=None
        if rule and low and high:
            scenarios=price_grid(low,high,rule['tax_exempt'])
            if not scenarios or max(s['net_per_unit'] for s in scenarios)<=0:
                flags.append('NO_POSITIVE_AFTER_TAX_SPREAD')
            # Historical averages are not executable pairs. Keep this descriptive only.
            pairs=activity['pairs']
            if pairs:
                positive_bars=sum(
                    Decimal(str(h))-(0 if rule['tax_exempt'] else min(
                        (Decimal(str(h))/50).to_integral_value(rounding=ROUND_FLOOR),Decimal(5_000_000)))
                    >Decimal(str(l)) for l,h in pairs)
        rows.append(dict(item_id=int(ident),name=meta['name'] if meta else 'Unknown ID '+ident,
                         members=meta['members'] if meta else None,
                         public_limit=meta['public_limit'] if meta else None,
                         low=low,high=high,low_age=ages['low'],high_age=ages['high'],
                         activity=activity,rule=rule,scenarios=scenarios,
                         positive_average_spread_intervals=positive_bars,
                         compared_intervals=len(activity['pairs']),
                         flags=sorted(set(flags)),status='EXCLUDED' if flags else 'OBSERVABLE_CANDIDATE',
                         sizing_status='NOT_ACCOUNT_QUALIFIED'))
    # Not a profit forecast: only a named descriptive ROI sort among qualified observations.
    rows.sort(key=lambda r:(bool(r['flags']),-(r['scenarios'][0]['conditional_roi_bps'] if r['scenarios'] else -10**12),r['item_id']))
    manifest=[dict(id=c['id'],digest=c['digest'],received_at=c['received_at'],confirmed_at=c['confirmed_at'],contract_hash=sha(c['contract'])) for c in caps if c]
    valid_until=now+60
    if namespace=='public':
        valid_until=min([valid_until]+[c['contract']['expires_at'] for c in caps if c])
    report=dict(valid_until=valid_until,engine=ENGINE_VERSION,namespace=namespace,as_of=now,
        window_start=snapshot['window_start'],window_end=snapshot['window_end'],
        expected_intervals=snapshot['expected_intervals'],received_intervals=len(snapshot['bars']),
        global_flags=global_flags,rows=rows,policy=asdict(policy),policy_hash=sha(asdict(policy)),
        rules_hash=sha(rules),capture_manifest=manifest,
        ranking='After-tax unit ROI at quoted references, among eligible observations; NOT expected portfolio profit',
        notice='Reported units are not an order book or complete-market census. Historical averages are not executable spreads. No calibrated fill probabilities or GP/hour.',
        live_verified=False if namespace=='fixture' or global_flags else None,
        source_status='SYNTHETIC_REPLAY' if namespace=='fixture' else ('BLOCKED_OR_UNREVIEWED' if global_flags else 'CAPTURED_WITH_LOCAL_REVIEW'),
        recommendation_status='RESEARCH_ONLY_NOT_ACCOUNT_QUALIFIED')
    report['report_hash']=sha(report)
    return report


def explore(row,buy,sell,quantity,ledger_state):
    if row['flags']:
        raise ValidationError('Cannot model an eligible opportunity from excluded data: '+', '.join(row['flags']))
    integer(quantity,'Scenario quantity',1)
    rule=row['rule']
    base=unit_scenario(buy,sell,rule['tax_exempt'])
    total=integer(buy*quantity,'Scenario capital',1)
    profit=base['net_per_unit']*quantity
    if abs(profit)>MAX_INTEGER:
        raise ValidationError('Conditional result exceeds supported integer range.')
    totals=summary(ledger_state)
    constraints=['ACCOUNT_OPENING_STATE_NOT_RECONCILED','ACCOUNT_REMAINING_ALLOWANCE_UNKNOWN',
                 'MEMBERSHIP_NOT_CONFIRMED','FUTURE_EXIT_SLOT_PATH_NOT_QUALIFIED']
    if total>totals['free_cash']:constraints.append('INSUFFICIENT_RECORDED_CASH')
    if total+totals['committed_at_cost']>ledger_state['exposure_cap']:constraints.append('EXPOSURE_CEILING_EXCEEDED')
    if totals['available_slots']<1:constraints.append('NO_RECORDED_FREE_SLOT')
    if row['public_limit'] is not None and quantity>row['public_limit']:
        constraints.append('QUANTITY_EXCEEDS_PUBLIC_ITEM_LIMIT')
    if base['net_per_unit']<=0:constraints.append('NONPOSITIVE_AFTER_TAX_MARGIN')
    stresses=[]
    for bps in (0,100,300,500):
        stressed=max(1,sell*(10000-bps)//10000)
        result=(stressed-tax_per_unit(stressed,rule['tax_exempt'])-buy)*quantity
        stresses.append(dict(exit_decline_bps=bps,sale_price=stressed,conditional_net=result))
    low=row['low']
    ref_loss=(low-tax_per_unit(low,rule['tax_exempt'])-buy)*quantity
    a=row['activity']
    return dict(item_id=row['item_id'],name=row['name'],quantity=quantity,capital=total,
        scenario=base,conditional_net=profit,stress_scenarios=stresses,
        lower_quote_exit_scenario=ref_loss,
        lower_quote_exit_notice='Hypothetical sale at the last lower-side quote; NOT guaranteed immediate liquidation.',
        entry_share_of_observed_units_bps=(quantity*10000//a['low_units']) if a['low_units'] else None,
        exit_share_of_observed_units_bps=(quantity*10000//a['high_units']) if a['high_units'] else None,
        constraint_flags=constraints,sizing_status='NOT_ACCOUNT_QUALIFIED',
        note='Stress moves are user-facing scenarios, NOT probabilities, price forecasts, or maximum possible loss. Reported-volume shares are not fill probabilities.',
        recommended_quantity=None,expected_profit=None,fill_probability=None)


def receipt(report,state,scenario=None):
    # The entire as-of report is fixed, including rejections and all assumptions.
    return dict(schema='osrs-decision-0.3',namespace=report['namespace'],
                report=report,recorded_account_state=state,account_state_hash=sha(state),
                scenario=scenario,executed=False,
                note='Saved research decision, NOT a placed offer. Paper and real namespaces remain separate.')


def journal_evidence(backup):
    offers=backup['state']['offers'].values()
    by_side={}
    for side in ('BUY','SELL'):
        group=[o for o in offers if o['side']==side]
        by_side[side]=dict(attempts=len(group),filled_in_full=sum(o['filled']==o['quantity'] for o in group),
            cancelled_zero_fill=sum(o['filled']==0 and o['status'] in ('CANCELLED','CLOSED') for o in group),
            partial_or_unfinished=sum(o['filled']<o['quantity'] and o['status']=='OPEN' for o in group),
            total_submitted_units=sum(o['quantity'] for o in group),total_filled_units=sum(o['filled'] for o in group))
    return dict(mode=backup['mode'],by_side=by_side,realized_net=backup['state']['realized_profit'],
                evidence_status='NO_PROSPECTIVE_POLICY_COMPARISON',
                notes=['Buy and sell attempts are not complete-flip counts.',
                       'Cancelled unfilled offers do not prove they would never fill.',
                       'Recording timestamps are not exact server execution times.',
                       'No trade-frequency, GP/hour, calibrated probability, or superiority inferred.'])


def inventory_valuation(state,report):
    """Reference-price scenario for ALL held units; unknown values stay unknown.

    Includes collected, uncollected purchased and sell-reserved items exactly
    once. Never mark an unsold item at its optimistic asking price. The quoted
    lower side is still not executable depth or a guaranteed liquidation price.
    """
    positions={}
    def add(ident,name,quantity,cost):
        if not quantity:return
        p=positions.setdefault(ident,dict(item_id=ident,name=name,quantity=0,cost=0))
        p['quantity']+=quantity;p['cost']+=cost
    for lot in state['lots']:add(lot['item_id'],lot['name'],lot['quantity'],lot['cost'])
    for o in state['offers'].values():
        add(o['item_id'],o['name'],o['pending_qty'],o['pending_cost'])
        add(o['item_id'],o['name'],o['reserved_qty'],o['reserved_cost'])
    rows={r['item_id']:r for r in report['rows']}
    known_value,unknown_count=0,0
    for p in positions.values():
        row=rows.get(p['item_id'])
        p['reference_value']=None;p['unrealized_scenario_net']=None
        if (not report['global_flags'] and row and row['rule'] and row['low']
                and row['low_age'] is not None and 0<=row['low_age']<=report['policy']['max_quote_age_seconds']):
            value=p['quantity']*(row['low']-tax_per_unit(row['low'],row['rule']['tax_exempt']))
            if value<=MAX_INTEGER:
                p['reference_value']=value;p['unrealized_scenario_net']=value-p['cost'];known_value+=value
            else:unknown_count+=1
        else:unknown_count+=1
    totals=summary(state)
    equity=totals['free_cash']+totals['escrow']+totals['claimable_net_cash']+known_value if unknown_count==0 else None
    if equity is not None and equity>MAX_INTEGER:equity=None
    return dict(positions=list(positions.values()),unpriced_positions=unknown_count,
                known_reference_value=known_value,reference_equity=equity,
                reference_portfolio_net=(equity-state['initial_cash']) if equity is not None else None,
                provenance=report['source_status'],
                notice='Reference-price SCENARIO, not executable liquidation, complete account equity, or realized profit. Unpriced positions prevent a total. Unrecorded account activity is not inferred.')
