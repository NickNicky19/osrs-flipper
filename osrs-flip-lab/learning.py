"""Bounded public-data learning. Future interval statistics, never order fills.

The fixed ridge family uses only captured, completed buckets. New generations
need new matured outcomes; one challenger at a time receives a single later
shadow evaluation. No public feature is a personal execution label.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math

VERSION = 'public-future-interval-ridge-2'
FEATURE_SCHEMA = 'public-completed-5m-features-2'
TARGET_SCHEMA = 'future-hour-side-interval-1'
FEATURES = ('spread_bps', 'log_low_units', 'log_high_units', 'momentum_bps')
HORIZON_SECONDS = 3600
MIN_ACTION_GAP_SECONDS = 300
FEATURE_LOOKBACK_SECONDS = 300
MIN_EXAMPLES = 240
MIN_ITEMS = 5
MIN_NEW_MATURED = 64
MIN_NEW_CLOCKS = 24
MIN_DECISION_CLOCKS = 48
MIN_LABEL_COVERAGE_BPS = 8000
MAX_OBSERVATIONS = 150_000
MAX_PREDICTIONS = 10_000
MAX_MODELS = 4
RIDGE_FAMILY = (10.0, 30.0)
PROMOTION = dict(min_shadow_pairs=400, min_shadow_days=7,
                 min_shadow_coverage_bps=9000, min_heldout_improvement_bps=300,
                 min_shadow_improvement_bps=500, min_positive_blocks=3,
                 min_portfolio_clocks=100, max_drawdown_worsening_bps=0,
                 min_portfolio_equity_advantage_gp=500_000)


def _utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _first_observations(observations, cutoff):
    """One first-seen revision per item and bucket; never use a later rewrite."""
    first = {}
    for row in sorted(observations, key=lambda x: (x['received_at'], x['capture_hash'])):
        if row['received_at'] <= cutoff and row.get('source') == 'CAPTURED_5M':
            first.setdefault((row['item_id'], row['bucket_start']), row)
    return first


def feature_vector(current, previous):
    low, high = current.get('low_avg'), current.get('high_avg')
    if not all(isinstance(x, (int, float)) and x > 0 and math.isfinite(x) for x in (low, high)):
        return None
    lu, hu = current.get('low_units'), current.get('high_units')
    if not all(type(x) is int and x >= 0 for x in (lu, hu)):
        return None
    old = previous.get('low_avg') if previous else None
    momentum = 0 if not isinstance(old, (int, float)) or old <= 0 else 10_000 * (low / old - 1)
    return [max(-5000, min(5000, 10_000 * (high / low - 1))),
            math.log1p(lu), math.log1p(hu), max(-5000, min(5000, momentum))]


def examples(observations, cutoff):
    """One-hour-later completed bucket, with a five-minute action/availability gap."""
    first = _first_observations(observations, cutoff)
    result = []
    for (item, bucket), current in first.items():
        target = first.get((item, bucket + HORIZON_SECONDS))
        previous = first.get((item, bucket - FEATURE_LOOKBACK_SECONDS))
        if previous is not None and previous['received_at'] > current['received_at']:
            previous = None
        if target is None or current['received_at'] < bucket + 300:
            continue
        if (current['received_at'] + MIN_ACTION_GAP_SECONDS > bucket + HORIZON_SECONDS or
                target['received_at'] < bucket + HORIZON_SECONDS + 300 or
                target['received_at'] > cutoff):
            continue
        x = feature_vector(current, previous)
        later_low, later_high = target.get('low_avg'), target.get('high_avg')
        later_units, later_high_units = target.get('low_units'), target.get('high_units')
        if x is None or not all(isinstance(v, (int, float)) and v > 0 and math.isfinite(v)
                                for v in (later_low, later_high)):
            continue
        if type(later_units) is not int or later_units < 0 or type(later_high_units) is not int or later_high_units < 0:
            continue
        result.append(dict(item_id=item, decision_at=current['received_at'], bucket_start=bucket,
                           target_bucket=bucket + HORIZON_SECONDS, x=x,
                           target_received_at=target['received_at'],
                           y_price_bps=max(-5000, min(5000, 10_000 * (later_low / current['low_avg'] - 1))),
                           y_high_price_bps=max(-5000, min(5000, 10_000 * (later_high / current['high_avg'] - 1))),
                           y_log_units=math.log1p(later_units),
                           y_high_log_units=math.log1p(later_high_units),
                           baseline_log_units=math.log1p(current['low_units']),
                           baseline_high_log_units=math.log1p(current['high_units']),
                           current_low=current['low_avg'], target_low=later_low,
                           current_high=current['high_avg'],target_high=later_high,
                           current_units=current['low_units'], target_units=later_units,
                           current_high_units=current['high_units'],target_high_units=later_high_units))
    return sorted(result, key=lambda e: (e['decision_at'], e['item_id']))


def _solve(matrix, vector):
    n = len(vector)
    a = [list(row) + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-10:
            raise ValueError('Degenerate regression matrix')
        a[col], a[pivot] = a[pivot], a[col]
        divisor = a[col][col]
        a[col] = [v / divisor for v in a[col]]
        for row in range(n):
            if row != col:
                factor = a[row][col]
                a[row] = [a[row][j] - factor * a[col][j] for j in range(n + 1)]
    return [a[i][-1] for i in range(n)]


def _fit(x, y, penalty):
    n = len(x[0])
    gram = [[0.0] * (n + 1) for _ in range(n + 1)]
    rhs = [0.0] * (n + 1)
    for row, target in zip(x, y):
        z = [1.0] + row
        for i in range(n + 1):
            rhs[i] += z[i] * target
            for j in range(n + 1):
                gram[i][j] += z[i] * z[j]
    for i in range(1, n + 1):
        gram[i][i] += penalty
    return _solve(gram, rhs)


def _model_digest(model):
    keys=('version','feature_schema','target_schema','features','mean','scale',
          'price_weights','units_weights','high_price_weights','high_units_weights',
          'lambda_','seed','data_cutoff','generation','code_version',
          'feature_definitions','target','horizon_seconds','trained_at',
          'train_last_decision','validation_last_decision','holdout_first_decision',
          'samples','metrics')
    return hashlib.sha256(json.dumps({k:model.get(k) for k in keys},sort_keys=True,
                                  separators=(',',':')).encode()).hexdigest()


def compatible_model(model):
    return (isinstance(model,dict) and model.get('version')==VERSION and
            model.get('feature_schema')==FEATURE_SCHEMA and model.get('target_schema')==TARGET_SCHEMA and
            isinstance(model.get('metrics'),dict) and
            model.get('checksum')==_model_digest(model) and model.get('id')==model['checksum'][:16])


def predict(model, x):
    if not compatible_model(model):
        raise ValueError('Incompatible public model schema')
    return _predict_unchecked(model,x)


def _predict_unchecked(model, x):
    z = [1.0] + [(v - mean) / scale for v, mean, scale in zip(x, model['mean'], model['scale'])]
    price = sum(a * b for a, b in zip(z, model['price_weights']))
    units = sum(a * b for a, b in zip(z, model['units_weights']))
    high_price = sum(a * b for a, b in zip(z, model['high_price_weights']))
    high_units = sum(a * b for a, b in zip(z, model['high_units_weights']))
    return dict(price_return_bps=max(-5000, min(5000, price)),
                log_units=max(0, min(30, units)),
                high_price_return_bps=max(-5000, min(5000, high_price)),
                high_log_units=max(0, min(30, high_units)))


def _score(model, rows):
    if not rows:
        return None
    baseline = sum(abs(e['y_price_bps']) for e in rows) / len(rows)
    candidate = sum(abs(e['y_price_bps'] - _predict_unchecked(model, e['x'])['price_return_bps']) for e in rows) / len(rows)
    units_base = sum(abs(e['y_log_units'] - e['baseline_log_units']) for e in rows) / len(rows)
    units_model = sum(abs(e['y_log_units'] - _predict_unchecked(model, e['x'])['log_units']) for e in rows) / len(rows)
    high_base = sum(abs(e['y_high_price_bps']) for e in rows) / len(rows)
    high_model = sum(abs(e['y_high_price_bps'] - _predict_unchecked(model,e['x'])['high_price_return_bps']) for e in rows) / len(rows)
    high_units_base = sum(abs(e['y_high_log_units'] - e['baseline_high_log_units']) for e in rows) / len(rows)
    high_units_model = sum(abs(e['y_high_log_units'] - _predict_unchecked(model,e['x'])['high_log_units']) for e in rows) / len(rows)
    return dict(n=len(rows), baseline_price_mae_bps=baseline, candidate_price_mae_bps=candidate,
                baseline_log_units_mae=units_base, candidate_log_units_mae=units_model,
                baseline_high_price_mae_bps=high_base,candidate_high_price_mae_bps=high_model,
                baseline_high_log_units_mae=high_units_base,candidate_high_log_units_mae=high_units_model)


def readiness_report(state, cutoff):
    rows=examples(state.get('observations',[]),cutoff)
    first=_first_observations(state.get('observations',[]),cutoff)
    matured=sum(bucket+HORIZON_SECONDS+300<=cutoff and row['received_at']<=cutoff
                for (_,bucket),row in first.items())
    previous=state['models'][-1]['data_cutoff'] if state.get('models') else 0
    new=[r for r in rows if r['target_received_at']>previous]
    return dict(usable_examples=len(rows),required_examples=MIN_EXAMPLES,
        usable_items=len({r['item_id'] for r in rows}),required_items=MIN_ITEMS,
        decision_clocks=len({r['decision_at'] for r in rows}),required_decision_clocks=MIN_DECISION_CLOCKS,
        finalized_coverage_bps=len(rows)*10000//max(1,matured),
        required_coverage_bps=MIN_LABEL_COVERAGE_BPS,
        new_matured_examples=len(new),required_new_matured=MIN_NEW_MATURED,
        new_matured_clocks=len({r['decision_at'] for r in new}),required_new_clocks=MIN_NEW_CLOCKS,
        last_check_at=state.get('last_learning_check') or None,
        next_due_at=(state.get('last_learning_check') or cutoff)+86400,
        generation_count=len(state.get('models',[])),max_generations=MAX_MODELS,
        source_scope='First-seen captured completed 5m buckets for retained research items; historical downloads are excluded.')


def train_once(observations, cutoff, previous_cutoff=0, generation=0):
    if len(observations) > MAX_OBSERVATIONS:
        return dict(status='PAUSED', reason='OBSERVATION_LIMIT')
    rows = examples(observations, cutoff)
    first = _first_observations(observations, cutoff)
    matured = sum(bucket + HORIZON_SECONDS + 300 <= cutoff and
                  observation['received_at'] <= cutoff for (_, bucket), observation in first.items())
    coverage_bps = len(rows)*10000//max(1, matured)
    if (len(rows) < MIN_EXAMPLES or len({r['item_id'] for r in rows}) < MIN_ITEMS or
            len({r['decision_at'] for r in rows}) < MIN_DECISION_CLOCKS or
            coverage_bps < MIN_LABEL_COVERAGE_BPS):
        return dict(status='DATA_INSUFFICIENT', examples=len(rows), required=MIN_EXAMPLES,
                    decision_clocks=len({r['decision_at'] for r in rows}),
                    label_coverage_bps=coverage_bps, required_coverage_bps=MIN_LABEL_COVERAGE_BPS)
    new = [r for r in rows if r['target_received_at'] > previous_cutoff]
    if previous_cutoff and (len(new) < MIN_NEW_MATURED or len({r['decision_at'] for r in new}) < MIN_NEW_CLOCKS):
        return dict(status='NO_NEW_MATURED_DATA', new_matured=len(new), required=MIN_NEW_MATURED,
                    new_clocks=len({r['decision_at'] for r in new}),required_new_clocks=MIN_NEW_CLOCKS)
    # Time gaps remove labels overlapping adjacent development/evaluation boundaries.
    clocks = sorted({r['decision_at'] for r in rows})
    train_end = clocks[int(len(clocks) * .6)]
    validation_start = train_end + HORIZON_SECONDS
    validation_end = clocks[int(len(clocks) * .8)]
    holdout_start = validation_end + HORIZON_SECONDS
    training = [r for r in rows if r['decision_at'] < train_end and r['target_bucket'] < train_end
                and r['target_received_at'] < train_end]
    validation = [r for r in rows if validation_start <= r['decision_at'] < validation_end
                  and r['target_bucket'] < validation_end and r['target_received_at'] < validation_end]
    holdout = [r for r in rows if holdout_start <= r['decision_at'] and r['target_received_at'] <= cutoff]
    if min(map(len, (training, validation, holdout))) < 24:
        return dict(status='DATA_INSUFFICIENT', reason='CHRONOLOGICAL_SPLITS', examples=len(rows))
    mean = [sum(r['x'][j] for r in training) / len(training) for j in range(len(FEATURES))]
    scale = [max(1e-9, math.sqrt(sum((r['x'][j] - mean[j]) ** 2 for r in training) / len(training))) for j in range(len(FEATURES))]
    x = [[(v - m) / s for v, m, s in zip(r['x'], mean, scale)] for r in training]
    penalty=RIDGE_FAMILY[generation % len(RIDGE_FAMILY)]
    model = dict(version=VERSION, feature_schema=FEATURE_SCHEMA,target_schema=TARGET_SCHEMA,
                 generation=generation+1,features=list(FEATURES), mean=mean, scale=scale,
                 code_version='public-pipeline-1',
                 feature_definitions='spread of reported 5m side averages; log1p units on each side; prior captured low-side momentum only when available at decision clock',
                 price_weights=_fit(x, [r['y_price_bps'] for r in training],penalty),
                 units_weights=_fit(x, [r['y_log_units'] for r in training],penalty),
                 high_price_weights=_fit(x,[r['y_high_price_bps'] for r in training],penalty),
                 high_units_weights=_fit(x,[r['y_high_log_units'] for r in training],penalty),
                 lambda_=penalty, seed=0, target='completed 5m lower/higher side average prices and reported side units one hour after completed decision bucket; no fill forecast',
                 horizon_seconds=HORIZON_SECONDS, data_cutoff=cutoff, trained_at=cutoff,
                 train_last_decision=training[-1]['decision_at'], validation_last_decision=validation[-1]['decision_at'],
                 holdout_first_decision=holdout[0]['decision_at'], samples=dict(train=len(training), validation=len(validation), holdout=len(holdout)))
    model['metrics'] = dict(validation=_score(model, validation), holdout=_score(model, holdout))
    model['checksum']=_model_digest(model)
    model['id'] = model['checksum'][:16]
    model['status'] = 'SHADOW'
    return dict(status='ML_SHADOW', model=model)


def record_predictions(state, current_rows, now):
    if state.get('public_pause') or state.get('kill_switch') or state.get('source_status') != 'CAPTURED_WITH_REVIEW':
        return 0
    model = next((m for m in reversed(state.get('models', [])) if m['status'] in ('SHADOW', 'RESEARCH_ACTIVE')), None)
    if model is None:
        return 0
    count = 0
    known = {(p['model_id'], p['item_id'], p['bucket_start']) for p in state['predictions']}
    for row in current_rows[:3]:
        x = feature_vector(row, row.get('previous'))
        target_bucket=row['bucket_start']+HORIZON_SECONDS
        if x is None or now < row['bucket_start'] + 300 or now + MIN_ACTION_GAP_SECONDS > target_bucket:
            continue
        key = (model['id'], row['item_id'], row['bucket_start'])
        if key in known:
            continue
        if len(state['predictions']) >= MAX_PREDICTIONS:
            state['learning_status'] = 'PAUSED'
            break
        state['predictions'].append(dict(model_id=model['id'], item_id=row['item_id'], bucket_start=row['bucket_start'],
                                         issued_at=now, target_bucket=target_bucket,
                                         feature_schema=FEATURE_SCHEMA,target_schema=TARGET_SCHEMA,
                                         baseline_low=row['low_avg'], baseline_log_units=math.log1p(row['low_units']),
                                         baseline_high=row['high_avg'],baseline_high_log_units=math.log1p(row['high_units']),
                                         forecast=predict(model, x), evidence='PREOBSERVED_PUBLIC_INTERVAL'))
        count += 1
    return count


def shadow_metrics(state, cutoff):
    first = _first_observations(state['observations'], cutoff)
    grouped = defaultdict(list)
    denominators=defaultdict(int)
    for p in state['predictions']:
        if p.get('target_schema') != TARGET_SCHEMA or p['issued_at'] + MIN_ACTION_GAP_SECONDS > p['target_bucket']:
            continue
        if p['target_bucket'] + 300 > cutoff:
            continue
        denominators[p['model_id']]+=1
        target = first.get((p['item_id'], p['target_bucket']))
        if target is None or target['received_at'] < p['target_bucket']+300 or target.get('low_avg') is None or target.get('high_avg') is None:
            continue
        actual = 10000 * (target['low_avg'] / p['baseline_low'] - 1)
        high_actual=10000*(target['high_avg']/p['baseline_high']-1)
        if type(target.get('low_units')) is not int or type(target.get('high_units')) is not int:
            continue
        grouped[p['model_id']].append((p, actual, high_actual, target))
    result = {}
    for model_id, pairs in grouped.items():
        n = len(pairs)
        base = sum(abs(actual) for _, actual, _, _ in pairs) / n
        ml = sum(abs(actual - p['forecast']['price_return_bps']) for p, actual, _, _ in pairs) / n
        high_base=sum(abs(actual) for _, _, actual, _ in pairs)/n
        high_ml=sum(abs(actual-p['forecast']['high_price_return_bps']) for p, _, actual, _ in pairs)/n
        units_base = sum(abs(math.log1p(t['low_units']) - p['baseline_log_units']) for p, _, _, t in pairs) / n
        units_ml = sum(abs(math.log1p(t['low_units']) - p['forecast']['log_units']) for p, _, _, t in pairs) / n
        high_units_base=sum(abs(math.log1p(t['high_units'])-p['baseline_high_log_units']) for p,_,_,t in pairs)/n
        high_units_ml=sum(abs(math.log1p(t['high_units'])-p['forecast']['high_log_units']) for p,_,_,t in pairs)/n
        by_day = defaultdict(list)
        for p, actual, _, _ in pairs:
            by_day[p['issued_at'] // 86400].append((p, actual))
        positive_days = sum(sum(abs(a) - abs(a - p['forecast']['price_return_bps']) for p, a in ps) > 0 for ps in by_day.values())
        result[model_id] = dict(n=n, days=len(by_day),coverage_bps=n*10000//max(1,denominators[model_id]),
                                matured_issued=denominators[model_id],
                                baseline_price_mae_bps=base, candidate_price_mae_bps=ml,
                                baseline_high_price_mae_bps=high_base,candidate_high_price_mae_bps=high_ml,
                                baseline_log_units_mae=units_base, candidate_log_units_mae=units_ml,
                                baseline_high_log_units_mae=high_units_base,candidate_high_log_units_mae=high_units_ml,
                                positive_days=positive_days, scope='captured interval statistics; no order fills')
    return result


def shadow_portfolio_comparison(state, cutoff):
    """Reference marks only; excluded from the execution-economics promotion gate."""
    first = _first_observations(state['observations'], cutoff)
    decisions = [d for d in state.get('shadow_decisions', []) if d['model_id'] ==
                 next((m['id'] for m in reversed(state['models']) if m['status'] == 'SHADOW'), None)]
    scores = []
    for decision in decisions:
        outcomes = {}
        for item in {p['item_id'] for p in decision['baseline'] + decision['candidate']}:
            target = first.get((item, decision['target_bucket']))
            if target is None or target['received_at'] <= decision['issued_at'] or target.get('low_avg') is None:
                break
            outcomes[item] = target['low_avg']
        else:
            def mark(plans):
                return sum((outcomes[p['item_id']] - p['buy']) * p['quantity'] for p in plans)
            scores.append((mark(decision['baseline']), mark(decision['candidate'])))
    if not decisions:
        return None
    capital = 500_000_000
    baseline = sum(x for x, _ in scores)
    candidate = sum(y for _, y in scores)
    base_worst = min((x for x, _ in scores), default=0)
    cand_worst = min((y for _, y in scores), default=0)
    return dict(evidence_class='REFERENCE_MARK_NO_FILLS',clocks=len(scores), issued_clocks=len(decisions),
                coverage_bps=len(scores)*10000//len(decisions),
                matched_capital_and_slots=all(d['capital']==capital and d['slots']==4 for d in decisions),
                baseline_reference_mark_gp=baseline, candidate_reference_mark_gp=candidate,
                worst_single_mark_worsening_bps=max(0, (base_worst-cand_worst)*10000/capital),
                assumption='Full immediate reference-price acquisition and next-bucket lower-side average mark; no fills, taxes on executions, queue, or actual P&L.')


def economic_shadow_comparison(state, cutoff):
    """One bounded forward-policy simulation on preissued selections, if covered.

    The interval trigger and publication delay remain unverified assumptions.
    Unknown shared allowance groups prevent automatic research promotion.
    """
    from simulator import replay
    model=next((m for m in reversed(state['models']) if m['status']=='SHADOW'),None)
    if model is None:return None
    matured=[d for d in state.get('shadow_decisions',[]) if d['model_id']==model['id'] and
             d['issued_at']+HORIZON_SECONDS+300<=cutoff]
    selected=sorted(matured,key=lambda d:d['issued_at'])[:100]
    if len(selected)<100:return dict(evidence_class='DATA_INSUFFICIENT',reason='FEWER_THAN_100_MATURED_DECISION_CLOCKS',
                                      clocks=len(selected),coverage_bps=0)
    published=[d for d in selected if type(d.get('public_available_at')) is int and
               d['public_available_at']>=d['issued_at']]
    if len(published)<100:
        return dict(evidence_class='DATA_INSUFFICIENT',reason='MISSING_VERIFIED_PUBLICATION_CLOCKS',
                    clocks=len(selected),coverage_bps=len(published)*10000//len(selected))
    first=_first_observations(state['observations'],cutoff)
    items={p['item_id'] for d in selected for p in d['baseline']+d['candidate']}
    earliest=min(d['issued_at'] for d in selected)
    latest=max(d['issued_at']+HORIZON_SECONDS for d in selected)
    bars=[dict(row) for (item,bucket),row in first.items() if item in items and
          earliest<=bucket<=latest]
    bars.sort(key=lambda b:(b['bucket_start'],b['item_id']))
    if len(bars)>12_000:return dict(evidence_class='DATA_INSUFFICIENT',reason='SIMULATION_BAR_BUDGET',
                                   clocks=len(selected),coverage_bps=0)
    def plans(side):
        result=[]
        for d in selected:
            for p in d[side]:
                result.append(dict(p,id=f"{model['id']}:{side}:{d['issued_at']}:{p['item_id']}",
                                   issued_at=d['issued_at'],activation_at=d['public_available_at']+60,
                                   exit_deadline=d['issued_at']+HORIZON_SECONDS))
        return result
    baseline_plans=plans('baseline');candidate_plans=plans('candidate')
    base=replay(baseline_plans,bars,publication_delay=0,action_delay=0,
                allow_expired_no_action=True)
    cand=replay(candidate_plans,bars,publication_delay=0,action_delay=0,
                allow_expired_no_action=True)
    if base['status']!='HISTORICAL_SIMULATION' or cand['status']!='HISTORICAL_SIMULATION':
        return dict(evidence_class='DATA_INSUFFICIENT',reason='REPLAY_INPUT_OR_COVERAGE',clocks=len(selected),
                    coverage_bps=0)
    return dict(evidence_class='FORWARD_ASSUMPTION_SIMULATION',clocks=len(selected),
        coverage_bps=10000,matched_capital_and_slots=True,
        tax_and_inventory_accounted=True,
        allowance_groups_verified=all(p.get('limit_group_status')=='VERIFIED_ITEM_ONLY'
                                      for p in baseline_plans+candidate_plans),
        baseline_after_tax_realized_gp=base['realized_after_tax_gp'],
        candidate_after_tax_realized_gp=cand['realized_after_tax_gp'],
        baseline_modeled_sold_units=base['modeled_sold_units'],
        candidate_modeled_sold_units=cand['modeled_sold_units'],
        baseline_expired_before_action=base['expired_before_action_count'],
        candidate_expired_before_action=cand['expired_before_action_count'],
        baseline_terminal_equity_gp=base['uncertain_total_equity_gp'],
        candidate_terminal_equity_gp=cand['uncertain_total_equity_gp'],
        baseline_open_stock_cost_gp=base['open_stock_cost_gp'],
        candidate_open_stock_cost_gp=cand['open_stock_cost_gp'],
        baseline_drawdown_bps=base['drawdown_bps'],candidate_drawdown_bps=cand['drawdown_bps'],
        drawdown_worsening_bps=(None if base['drawdown_bps'] is None or cand['drawdown_bps'] is None else
                                max(0,cand['drawdown_bps']-base['drawdown_bps'])),
        assumptions=base['assumptions'],
        scope='Prospectively saved policy selections. Activation is verified Pages deploy completion plus a 60-second hypothetical manual-action delay. Expired quotes yield zero action. Later interval-cross fills remain retrospective assumptions, not actual profit.')


def promotion_gate(model, shadow, portfolio):
    """One predeclared look per frozen candidate. No threshold or risk retuning."""
    reasons = []
    if not compatible_model(model):
        reasons.append('MODEL_SCHEMA_OR_CHECKSUM')
    holdout = model['metrics']['holdout']
    if holdout['candidate_price_mae_bps'] > holdout['baseline_price_mae_bps'] * (1 - PROMOTION['min_heldout_improvement_bps'] / 10000):
        reasons.append('HELDOUT_FORECAST')
    if holdout['candidate_log_units_mae'] >= holdout['baseline_log_units_mae']:
        reasons.append('HELDOUT_UNITS')
    if (holdout['candidate_high_price_mae_bps'] >= holdout['baseline_high_price_mae_bps'] or
            holdout['candidate_high_log_units_mae'] >= holdout['baseline_high_log_units_mae']):
        reasons.append('HELDOUT_EXIT_SIDE')
    if shadow is None or shadow['n'] < PROMOTION['min_shadow_pairs'] or shadow['days'] < PROMOTION['min_shadow_days']:
        reasons.append('UNTOUCHED_SHADOW_COVERAGE')
    elif shadow.get('coverage_bps',0)<PROMOTION['min_shadow_coverage_bps']:
        reasons.append('UNTOUCHED_SHADOW_LABEL_COVERAGE')
    elif shadow['candidate_price_mae_bps'] > shadow['baseline_price_mae_bps'] * (1 - PROMOTION['min_shadow_improvement_bps'] / 10000) or shadow['positive_days'] < PROMOTION['min_positive_blocks']:
        reasons.append('UNTOUCHED_SHADOW_PERFORMANCE')
    if shadow is not None and shadow.get('candidate_log_units_mae', math.inf) >= shadow.get('baseline_log_units_mae', -math.inf):
        reasons.append('UNTOUCHED_SHADOW_UNITS')
    if shadow is not None and (shadow.get('candidate_high_price_mae_bps',math.inf)>=shadow.get('baseline_high_price_mae_bps',-math.inf) or
                               shadow.get('candidate_high_log_units_mae',math.inf)>=shadow.get('baseline_high_log_units_mae',-math.inf)):
        reasons.append('UNTOUCHED_SHADOW_EXIT_SIDE')
    if (not portfolio or portfolio.get('evidence_class') != 'FORWARD_ASSUMPTION_SIMULATION' or
            portfolio.get('clocks', 0) < PROMOTION['min_portfolio_clocks'] or
            portfolio.get('coverage_bps', 0) < PROMOTION['min_shadow_coverage_bps'] or
            not portfolio.get('matched_capital_and_slots') or not portfolio.get('tax_and_inventory_accounted')):
        reasons.append('MATCHED_PORTFOLIO_EVIDENCE')
    elif (not portfolio.get('allowance_groups_verified') or portfolio.get('baseline_terminal_equity_gp') is None or
          portfolio.get('candidate_terminal_equity_gp') is None or portfolio.get('drawdown_worsening_bps') is None):
        reasons.append('ECONOMIC_ASSUMPTION_OR_EQUITY_GAP')
    elif (portfolio.get('baseline_modeled_sold_units',0)<1 or portfolio.get('candidate_modeled_sold_units',0)<1 or
          portfolio.get('candidate_after_tax_realized_gp', -math.inf) < portfolio.get('baseline_after_tax_realized_gp', math.inf) or
          portfolio.get('candidate_terminal_equity_gp',-math.inf)<portfolio.get('baseline_terminal_equity_gp',math.inf)+PROMOTION['min_portfolio_equity_advantage_gp'] or
          portfolio.get('drawdown_worsening_bps', math.inf) > PROMOTION['max_drawdown_worsening_bps']):
        reasons.append('MATCHED_PORTFOLIO_CONSEQUENCES')
    return dict(pass_all=not reasons, reasons=reasons, thresholds=PROMOTION.copy(),
                note='Even a matched fill-assumption simulation cannot certify actual profit.')


def daily_readiness(state, now):
    if state.get('kill_switch') or state.get('public_pause') or state.get('source_status') != 'CAPTURED_WITH_REVIEW':
        state['learning_status'] = 'PAUSED'
        state['learning_reason'] = dict(status='PAUSED',reason='SOURCE_OR_KILL_SWITCH')
        return 'PAUSED'
    if now - state.get('last_learning_check', 0) < 86400:
        return state.get('learning_status', 'RULES_BASELINE')
    state['last_learning_check'] = now
    if len(state['models']) > MAX_MODELS or len(state['observations']) > MAX_OBSERVATIONS:
        state['learning_status'] = 'PAUSED'
        state['learning_reason'] = dict(status='PAUSED',reason='COMPUTE_BUDGET')
        return 'PAUSED'
    for model in state['models']:
        if model.get('status') in ('SHADOW','RESEARCH_ACTIVE') and not compatible_model(model):
            model['status']='CORRUPT_OR_INCOMPATIBLE'
            if state.get('active_model_id')==model.get('id'):
                state['active_model_id']=None
            state['learning_reason']=dict(status='PAUSED',reason='MODEL_SCHEMA_OR_CHECKSUM')
    previous = state['models'][-1]['data_cutoff'] if state['models'] else 0
    if any(m['status'] == 'SHADOW' for m in state['models']):
        state['shadow_metrics'] = shadow_metrics(state, now)
        state['portfolio_shadow_comparison'] = shadow_portfolio_comparison(state, now)
        model = next(m for m in reversed(state['models']) if m['status'] == 'SHADOW')
        shadow = state['shadow_metrics'].get(model['id'])
        portfolio = state.get('portfolio_shadow_comparison')
        ready = (shadow and shadow['n'] >= PROMOTION['min_shadow_pairs'] and
                 shadow['days'] >= PROMOTION['min_shadow_days'] and
                 shadow.get('coverage_bps',0) >= PROMOTION['min_shadow_coverage_bps'] and
                 portfolio and portfolio['clocks'] >= PROMOTION['min_portfolio_clocks'])
        if ready and 'promotion_look_at' not in model:
            model['promotion_look_at'] = now
            # A reference-price mark cannot satisfy the after-tax execution gate.
            state['economic_shadow_comparison']=economic_shadow_comparison(state,now)
            gate = promotion_gate(model, shadow, state.get('economic_shadow_comparison'))
            state['promotion_gate'] = gate
            state['last_evaluation_at']=now
            if gate['pass_all'] and not state.get('kill_switch'):
                for prior_model in state['models']:
                    if prior_model.get('status')=='RESEARCH_ACTIVE':
                        prior_model['status']='SUPERSEDED'
                model['status'] = 'RESEARCH_ACTIVE'
                state['active_model_id'] = model['id']
                state['learning_status'] = 'ML_RESEARCH_ACTIVE'
            else:
                model['status'] = 'SHADOW_REJECTED'
                state['learning_status'] = 'ML_RESEARCH_ACTIVE' if state.get('active_model_id') else 'RULES_BASELINE'
                state['learning_reason']=dict(status=state['learning_status'],reason='PROMOTION_GATE_FAILED',details=gate['reasons'])
        else:
            state['learning_status'] = 'ML_RESEARCH_ACTIVE' if state.get('active_model_id') else 'ML_SHADOW'
        return state['learning_status']
    if state.get('active_model_id'):
        state['shadow_metrics'] = shadow_metrics(state, now)
        current = state['shadow_metrics'].get(state['active_model_id'])
        if current and current['n'] >= PROMOTION['min_shadow_pairs'] and current['days'] >= 3 and (
            current.get('coverage_bps',0)<PROMOTION['min_shadow_coverage_bps'] or
            current['candidate_price_mae_bps'] > current['baseline_price_mae_bps'] * 1.5 or
            current['candidate_log_units_mae'] > current['baseline_log_units_mae'] * 1.5 or
            current.get('candidate_high_price_mae_bps',math.inf) > current.get('baseline_high_price_mae_bps',0)*1.5 or
            current.get('candidate_high_log_units_mae',math.inf) > current.get('baseline_high_log_units_mae',0)*1.5):
            rollback(state)
            state['learning_reason'] = dict(status='RULES_BASELINE', reason='SHADOW_DRIFT_ROLLBACK')
    if len(state['models']) >= MAX_MODELS:
        state['learning_status'] = 'ML_RESEARCH_ACTIVE' if state.get('active_model_id') else 'PAUSED'
        state['learning_reason'] = dict(status='PAUSED',reason='MAX_REVIEWED_GENERATIONS_REACHED',max_models=MAX_MODELS)
        return state['learning_status']
    result = train_once(state['observations'], now, previous, len(state['models']))
    if result['status'] == 'ML_SHADOW':
        state['models'].append(result['model'])
        state['learning_status'] = 'ML_RESEARCH_ACTIVE' if state.get('active_model_id') else 'ML_SHADOW'
        state['last_training_at'] = now
        state['last_evaluation_at'] = now
        state['latest_data_cutoff'] = now
        state['learning_reason']=dict(status='ML_SHADOW',reason='NEW_MATURED_DATA',
            examples=sum(result['model']['samples'].values()),
            required_examples=MIN_EXAMPLES,required_items=MIN_ITEMS,
            required_decision_clocks=MIN_DECISION_CLOCKS,generation=result['model']['generation'])
    else:
        state['learning_status'] = result['status'] if not state.get('active_model_id') else 'ML_RESEARCH_ACTIVE'
        state['learning_reason'] = result
    return state['learning_status']


def rollback(state):
    state['active_model_id'] = None
    for model in state.get('models', []):
        if model['status'] == 'RESEARCH_ACTIVE':
            model['status'] = 'ROLLED_BACK'
    state['learning_status'] = 'RULES_BASELINE'
    return state
