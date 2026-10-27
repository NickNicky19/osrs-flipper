"""Bounded public-data learning. Forecasts interval statistics, never order fills.

The one fixed candidate is a small ridge regression. Features and scaling are fit
on the chronological training slice only. A held-out slice is used once for the
candidate decision; later pre-observation shadow records are a separate gate.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math

VERSION = 'public-interval-ridge-1'
FEATURES = ('spread_bps', 'log_low_units', 'log_high_units', 'momentum_bps')
HORIZON_SECONDS = 300
MIN_EXAMPLES = 240
MIN_ITEMS = 5
MIN_NEW_MATURED = 64
MIN_DECISION_CLOCKS = 48
MIN_LABEL_COVERAGE_BPS = 8000
MAX_OBSERVATIONS = 150_000
MAX_PREDICTIONS = 10_000
MAX_MODELS = 1
RIDGE_LAMBDA = 10.0
PROMOTION = dict(min_shadow_pairs=400, min_shadow_days=7,
                 min_shadow_coverage_bps=9000, min_heldout_improvement_bps=300,
                 min_shadow_improvement_bps=500, min_positive_blocks=3,
                 min_portfolio_clocks=100, max_drawdown_worsening_bps=0)


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
    """A decision requires a captured completed bucket and a later captured label."""
    first = _first_observations(observations, cutoff)
    result = []
    for (item, bucket), current in first.items():
        target = first.get((item, bucket + HORIZON_SECONDS))
        previous = first.get((item, bucket - HORIZON_SECONDS))
        if previous is not None and previous['received_at'] > current['received_at']:
            previous = None
        if target is None or current['received_at'] < bucket + HORIZON_SECONDS:
            continue
        if target['received_at'] <= current['received_at'] or target['received_at'] > cutoff:
            continue
        x = feature_vector(current, previous)
        later_low, later_units = target.get('low_avg'), target.get('low_units')
        if x is None or not isinstance(later_low, (int, float)) or later_low <= 0 or not math.isfinite(later_low):
            continue
        if type(later_units) is not int or later_units < 0:
            continue
        result.append(dict(item_id=item, decision_at=current['received_at'], bucket_start=bucket,
                           target_bucket=bucket + HORIZON_SECONDS, x=x,
                           target_received_at=target['received_at'],
                           y_price_bps=max(-5000, min(5000, 10_000 * (later_low / current['low_avg'] - 1))),
                           y_log_units=math.log1p(later_units),
                           baseline_log_units=math.log1p(current['low_units']),
                           current_low=current['low_avg'], target_low=later_low,
                           current_units=current['low_units'], target_units=later_units))
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


def _fit(x, y):
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
        gram[i][i] += RIDGE_LAMBDA
    return _solve(gram, rhs)


def predict(model, x):
    z = [1.0] + [(v - mean) / scale for v, mean, scale in zip(x, model['mean'], model['scale'])]
    price = sum(a * b for a, b in zip(z, model['price_weights']))
    units = sum(a * b for a, b in zip(z, model['units_weights']))
    return dict(price_return_bps=max(-5000, min(5000, price)),
                log_units=max(0, min(30, units)))


def _score(model, rows):
    if not rows:
        return None
    baseline = sum(abs(e['y_price_bps']) for e in rows) / len(rows)
    candidate = sum(abs(e['y_price_bps'] - predict(model, e['x'])['price_return_bps']) for e in rows) / len(rows)
    units_base = sum(abs(e['y_log_units'] - e['baseline_log_units']) for e in rows) / len(rows)
    units_model = sum(abs(e['y_log_units'] - predict(model, e['x'])['log_units']) for e in rows) / len(rows)
    return dict(n=len(rows), baseline_price_mae_bps=baseline, candidate_price_mae_bps=candidate,
                baseline_log_units_mae=units_base, candidate_log_units_mae=units_model)


def train_once(observations, cutoff, previous_cutoff=0):
    if len(observations) > MAX_OBSERVATIONS:
        return dict(status='PAUSED', reason='OBSERVATION_LIMIT')
    rows = examples(observations, cutoff)
    first = _first_observations(observations, cutoff)
    matured = sum(bucket + 2*HORIZON_SECONDS <= cutoff and
                  observation['received_at'] <= cutoff for (_, bucket), observation in first.items())
    coverage_bps = len(rows)*10000//max(1, matured)
    if (len(rows) < MIN_EXAMPLES or len({r['item_id'] for r in rows}) < MIN_ITEMS or
            len({r['decision_at'] for r in rows}) < MIN_DECISION_CLOCKS or
            coverage_bps < MIN_LABEL_COVERAGE_BPS):
        return dict(status='DATA_INSUFFICIENT', examples=len(rows), required=MIN_EXAMPLES,
                    decision_clocks=len({r['decision_at'] for r in rows}),
                    label_coverage_bps=coverage_bps, required_coverage_bps=MIN_LABEL_COVERAGE_BPS)
    new = sum(r['decision_at'] > previous_cutoff for r in rows)
    if previous_cutoff and new < MIN_NEW_MATURED:
        return dict(status='NO_NEW_MATURED_DATA', new_matured=new, required=MIN_NEW_MATURED)
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
    model = dict(version=VERSION, features=list(FEATURES), mean=mean, scale=scale,
                 code_version='public-pipeline-1',
                 feature_definitions='spread of reported 5m side averages; log1p units on each side; prior captured low-side momentum only when available at decision clock',
                 price_weights=_fit(x, [r['y_price_bps'] for r in training]),
                 units_weights=_fit(x, [r['y_log_units'] for r in training]),
                 lambda_=RIDGE_LAMBDA, seed=0, target='next captured 5m low-side average return in bps and log1p reported low-side units',
                 horizon_seconds=HORIZON_SECONDS, data_cutoff=cutoff, trained_at=cutoff,
                 train_last_decision=training[-1]['decision_at'], validation_last_decision=validation[-1]['decision_at'],
                 holdout_first_decision=holdout[0]['decision_at'], samples=dict(train=len(training), validation=len(validation), holdout=len(holdout)))
    model['metrics'] = dict(validation=_score(model, validation), holdout=_score(model, holdout))
    model['id'] = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:16]
    model['status'] = 'SHADOW'
    return dict(status='ML_SHADOW', model=model)


def record_predictions(state, current_rows, now):
    model = next((m for m in reversed(state.get('models', [])) if m['status'] in ('SHADOW', 'RESEARCH_ACTIVE')), None)
    if model is None:
        return 0
    count = 0
    known = {(p['model_id'], p['item_id'], p['bucket_start']) for p in state['predictions']}
    for row in current_rows[:3]:
        x = feature_vector(row, row.get('previous'))
        if x is None or now < row['bucket_start'] + 300:
            continue
        key = (model['id'], row['item_id'], row['bucket_start'])
        if key in known:
            continue
        if len(state['predictions']) >= MAX_PREDICTIONS:
            state['learning_status'] = 'PAUSED'
            break
        state['predictions'].append(dict(model_id=model['id'], item_id=row['item_id'], bucket_start=row['bucket_start'],
                                         issued_at=now, target_bucket=row['bucket_start'] + HORIZON_SECONDS,
                                         baseline_low=row['low_avg'], baseline_log_units=math.log1p(row['low_units']),
                                         forecast=predict(model, x), evidence='PREOBSERVED_PUBLIC_INTERVAL'))
        count += 1
    return count


def shadow_metrics(state, cutoff):
    first = _first_observations(state['observations'], cutoff)
    grouped = defaultdict(list)
    for p in state['predictions']:
        target = first.get((p['item_id'], p['target_bucket']))
        if target is None or target['received_at'] <= p['issued_at'] or target.get('low_avg') is None:
            continue
        actual = 10000 * (target['low_avg'] / p['baseline_low'] - 1)
        if type(target.get('low_units')) is not int:
            continue
        grouped[p['model_id']].append((p, actual, target))
    result = {}
    for model_id, pairs in grouped.items():
        n = len(pairs)
        base = sum(abs(actual) for _, actual, _ in pairs) / n
        ml = sum(abs(actual - p['forecast']['price_return_bps']) for p, actual, _ in pairs) / n
        units_base = sum(abs(math.log1p(t['low_units']) - p['baseline_log_units']) for p, _, t in pairs) / n
        units_ml = sum(abs(math.log1p(t['low_units']) - p['forecast']['log_units']) for p, _, t in pairs) / n
        by_day = defaultdict(list)
        for p, actual, _ in pairs:
            by_day[p['issued_at'] // 86400].append((p, actual))
        positive_days = sum(sum(abs(a) - abs(a - p['forecast']['price_return_bps']) for p, a in ps) > 0 for ps in by_day.values())
        result[model_id] = dict(n=n, days=len(by_day), baseline_price_mae_bps=base, candidate_price_mae_bps=ml,
                                baseline_log_units_mae=units_base, candidate_log_units_mae=units_ml,
                                positive_days=positive_days, scope='captured interval statistics; no order fills')
    return result


def shadow_portfolio_comparison(state, cutoff):
    """Matched-capital reference marks, deliberately not a fill or P&L model."""
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
    return dict(clocks=len(scores), issued_clocks=len(decisions),
                coverage_bps=len(scores)*10000//len(decisions),
                matched_capital_and_slots=all(d['capital']==capital and d['slots']==4 for d in decisions),
                baseline_reference_net=baseline, candidate_reference_net=candidate,
                drawdown_worsening_bps=max(0, (base_worst-cand_worst)*10000/capital),
                assumption='Full immediate reference-price acquisition and next-bucket lower-side average mark; no fills, taxes on executions, queue, or actual P&L.')


def promotion_gate(model, shadow, portfolio):
    """One predeclared look per frozen candidate. No threshold or risk retuning."""
    reasons = []
    holdout = model['metrics']['holdout']
    if holdout['candidate_price_mae_bps'] > holdout['baseline_price_mae_bps'] * (1 - PROMOTION['min_heldout_improvement_bps'] / 10000):
        reasons.append('HELDOUT_FORECAST')
    if holdout['candidate_log_units_mae'] >= holdout['baseline_log_units_mae']:
        reasons.append('HELDOUT_UNITS')
    if shadow is None or shadow['n'] < PROMOTION['min_shadow_pairs'] or shadow['days'] < PROMOTION['min_shadow_days']:
        reasons.append('UNTOUCHED_SHADOW_COVERAGE')
    elif shadow['candidate_price_mae_bps'] > shadow['baseline_price_mae_bps'] * (1 - PROMOTION['min_shadow_improvement_bps'] / 10000) or shadow['positive_days'] < PROMOTION['min_positive_blocks']:
        reasons.append('UNTOUCHED_SHADOW_PERFORMANCE')
    if shadow is not None and shadow.get('candidate_log_units_mae', math.inf) >= shadow.get('baseline_log_units_mae', -math.inf):
        reasons.append('UNTOUCHED_SHADOW_UNITS')
    if not portfolio or portfolio.get('clocks', 0) < PROMOTION['min_portfolio_clocks'] or portfolio.get('coverage_bps', 0) < PROMOTION['min_shadow_coverage_bps'] or not portfolio.get('matched_capital_and_slots'):
        reasons.append('MATCHED_PORTFOLIO_EVIDENCE')
    elif portfolio.get('candidate_reference_net', -math.inf) < portfolio.get('baseline_reference_net', math.inf) or portfolio.get('drawdown_worsening_bps', math.inf) > PROMOTION['max_drawdown_worsening_bps']:
        reasons.append('MATCHED_PORTFOLIO_CONSEQUENCES')
    return dict(pass_all=not reasons, reasons=reasons, thresholds=PROMOTION.copy(),
                note='Reference-price simulation is assumption-dependent; it cannot certify real profit.')


def daily_readiness(state, now):
    if state.get('kill_switch') or state.get('public_pause') or state.get('source_status') != 'CAPTURED_WITH_REVIEW':
        state['learning_status'] = 'PAUSED'
        return 'PAUSED'
    if now - state.get('last_learning_check', 0) < 86400:
        return state.get('learning_status', 'RULES_BASELINE')
    state['last_learning_check'] = now
    if len(state['models']) > MAX_MODELS or len(state['observations']) > MAX_OBSERVATIONS:
        state['learning_status'] = 'PAUSED'
        return 'PAUSED'
    if state.get('active_model_id'):
        state['shadow_metrics'] = shadow_metrics(state, now)
        current = state['shadow_metrics'].get(state['active_model_id'])
        if current and current['n'] >= PROMOTION['min_shadow_pairs'] and current['days'] >= 3 and (
            current['candidate_price_mae_bps'] > current['baseline_price_mae_bps'] * 1.5 or
            current['candidate_log_units_mae'] > current['baseline_log_units_mae'] * 1.5):
            rollback(state)
            state['learning_reason'] = dict(status='RULES_BASELINE', reason='SHADOW_DRIFT_ROLLBACK')
            return state['learning_status']
        state['learning_status'] = 'ML_RESEARCH_ACTIVE'
        return state['learning_status']
    if any(m['status'] == 'SHADOW_REJECTED' for m in state['models']):
        state['learning_status'] = 'RULES_BASELINE'
        return state['learning_status']
    if len(state['models']) >= MAX_MODELS and not any(m['status'] == 'SHADOW' for m in state['models']):
        state['learning_status'] = 'RULES_BASELINE'
        state['learning_reason'] = dict(status='NO_NEW_MATURED_DATA', reason='FIXED_CANDIDATE_SET_EXHAUSTED')
        return state['learning_status']
    previous = state['models'][-1]['data_cutoff'] if state['models'] else 0
    if any(m['status'] == 'SHADOW' for m in state['models']):
        state['shadow_metrics'] = shadow_metrics(state, now)
        state['portfolio_shadow_comparison'] = shadow_portfolio_comparison(state, now)
        model = next(m for m in reversed(state['models']) if m['status'] == 'SHADOW')
        shadow = state['shadow_metrics'].get(model['id'])
        portfolio = state.get('portfolio_shadow_comparison')
        ready = (shadow and shadow['n'] >= PROMOTION['min_shadow_pairs'] and
                 shadow['days'] >= PROMOTION['min_shadow_days'] and portfolio and
                 portfolio['clocks'] >= PROMOTION['min_portfolio_clocks'] and
                 portfolio['coverage_bps'] >= PROMOTION['min_shadow_coverage_bps'])
        if ready and 'promotion_look_at' not in model:
            model['promotion_look_at'] = now
            gate = promotion_gate(model, shadow, portfolio)
            state['promotion_gate'] = gate
            if gate['pass_all'] and not state.get('kill_switch'):
                model['status'] = 'RESEARCH_ACTIVE'
                state['active_model_id'] = model['id']
                state['learning_status'] = 'ML_RESEARCH_ACTIVE'
            else:
                model['status'] = 'SHADOW_REJECTED'
                state['learning_status'] = 'RULES_BASELINE'
        else:
            state['learning_status'] = 'ML_SHADOW'
        return state['learning_status']
    result = train_once(state['observations'], now, previous)
    if result['status'] == 'ML_SHADOW':
        state['models'].append(result['model'])
        state['learning_status'] = 'ML_SHADOW'
        state['last_training_at'] = now
        state['last_evaluation_at'] = now
        state['latest_data_cutoff'] = now
    else:
        state['learning_status'] = result['status']
        state['learning_reason'] = result
    return state['learning_status']


def rollback(state):
    state['active_model_id'] = None
    for model in state.get('models', []):
        if model['status'] == 'RESEARCH_ACTIVE':
            model['status'] = 'ROLLED_BACK'
    state['learning_status'] = 'RULES_BASELINE'
    return state
