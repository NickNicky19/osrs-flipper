"""Public-only pipeline regressions. Captured fixture is a reduced real v2 body."""
import copy
from dataclasses import replace
from datetime import datetime, timezone
from email.message import Message
from email.utils import format_datetime
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from market import SourceContract, parse_payload
from core import ValidationError
from public_pipeline import (BANKROLL, SourceClient, _parse_safe, cycle, grade_picks,
    graph_stats, issue_picks, load_state, mark_published, new_state, parse_timeseries,
    public_export, save_state, screen, compact_history, collect, _retry_delay,
    ProviderDeferred, recover_pending_publication, verified_deploy_completed_at)
from learning import (MIN_EXAMPLES, daily_readiness, examples, promotion_gate,
    rollback, shadow_metrics, train_once, feature_vector, predict, record_predictions,
    HORIZON_SECONDS, FEATURE_LOOKBACK_SECONDS, compatible_model,
    economic_shadow_comparison)


SAMPLE = json.loads((Path(__file__).parent/'fixtures'/'captured-v2-sample.json').read_text(encoding='utf-8'))
NOW = 1793132577


def observations(items=6, buckets=300, start=1791000000):
    result=[]
    for i in range(buckets):
        bucket=start+i*300
        for item in range(1,items+1):
            low=1000+item*100+i*2+(i%4)*3
            result.append(dict(item_id=item,bucket_start=bucket,low_avg=low,
                high_avg=low+20+(i%3),low_units=100+item*3+i%10,
                high_units=120+item*2+i%7,received_at=bucket+360,
                capture_hash=f'{i:04d}-{item}',source='CAPTURED_5M'))
    return result


def valid_row(item, now, qty=4, buy=1_000_000, sell=1_200_000):
    return dict(item_id=item,name='Test item '+str(item),flags=[],observed_instant_sell_low=buy,
        observed_instant_buy_high=sell,low_time=now-20,high_time=now-20,
        quantity_assumption=qty,required_cash=qty*buy,
        conditional_after_tax_gp=(sell-sell//50-buy)*qty,tax_exempt=False)


class FakeClient:
    def __init__(self, now, fail=False):
        self.now=now;self.calls=0;self.raw_bytes=0;self.fail=fail
        self.contract=SourceContract(api_version='v2',route_versions={k:'v2' for k in
            ('mapping','latest','5m','1h','24h','timeseries')},reviewed=True,
            reviewed_at=now-100,expires_at=now+86400,permission_reference='fixture',
            semantics_reference='fixture',user_agent='fixture-test')

    def fetch(self,kind,params=None,now=None):
        self.calls+=1
        if self.fail: raise ValueError('fixture outage')
        if kind=='mapping': raw=json.dumps([dict(id=4151,name='Abyssal whip',members=True,limit=70)]).encode()
        elif kind=='latest': raw=json.dumps(dict(data={'4151':dict(low=1_000_000,lowTime=self.now-20,high=1_200_000,highTime=self.now-20)})).encode()
        elif kind in ('5m','1h','24h'):
            seconds={'5m':300,'1h':3600,'24h':86400}[kind]
            stamp=(self.now//seconds-1)*seconds
            if params and 'timestamp' in params: stamp=params['timestamp']
            raw=json.dumps(dict(timestamp=stamp,data={'4151':dict(avgLowPrice=1_000_000,
                avgHighPrice=1_200_000,lowPriceVolume=200,highPriceVolume=200)})).encode()
        elif kind=='timeseries':
            raw=json.dumps(SAMPLE['timeseries']).encode()
        else: raise AssertionError(kind)
        self.raw_bytes+=len(raw)
        return raw,dict(kind=kind,version='v2',url='https://prices.runescape.wiki/api/v2/osrs/'+kind+
            (('?timestamp='+str(params['timestamp'])) if params and 'timestamp' in params else ''),
            requested_at=self.now,received_at=self.now,sha256=str(hash(raw)),bytes=len(raw),status=200)


class PublicPipelineTests(unittest.TestCase):
    def test_actual_captured_v2_formats_per_route(self):
        contract=SourceContract.from_dict(dict(api_version='v1',route_versions={k:'v2' for k in
            ('mapping','latest','5m','1h','24h','timeseries')}))
        self.assertEqual(contract.version_for('mapping'),'v2')
        for kind in ('mapping','latest','5m','1h','24h'):
            parsed=parse_payload(kind,json.dumps(SAMPLE[kind]).encode(),NOW,contract)
            self.assertIn('4151',parsed['items'])
        self.assertEqual(parse_payload('latest',json.dumps(SAMPLE['latest']).encode(),NOW,contract)['items']['4151']['low_time'],
                         SAMPLE['latest']['data']['4151']['lowTime'])
        series=parse_timeseries(json.dumps(SAMPLE['timeseries']).encode(),NOW,4151)
        self.assertEqual(series['timestep'],3600)
        self.assertEqual(series['availability'],'RETROSPECTIVE_DOWNLOAD')

    def test_item_quarantine_preserves_good_items(self):
        contract=SourceContract(api_version='v2')
        body=copy.deepcopy(SAMPLE['latest']);body['data']['999']={'high':'bad','highTime':1,'low':2,'lowTime':1}
        result=_parse_safe('latest',json.dumps(body).encode(),NOW,contract)
        self.assertIn('4151',result['items']);self.assertIn('999',result['rejected'])

    def test_nullable_hourly_volume_excludes_only_affected_items(self):
        contract=SourceContract(api_version='v2')
        hour=copy.deepcopy(SAMPLE['1h'])
        hour['data']['4151']['lowPriceVolume']=None
        hour['data']['26219']['highPriceVolume']=None
        latest_body=copy.deepcopy(SAMPLE['latest'])
        latest_body['data']['27616']['highTime']=NOW-20
        parsed=lambda kind, body: _parse_safe(kind,json.dumps(body).encode(),NOW,contract)
        mapping=parsed('mapping',SAMPLE['mapping'])['items']
        latest=parsed('latest',latest_body)
        five=parsed('5m',SAMPLE['5m'])
        hourly=parsed('1h',hour)
        rules=dict(version='public-rules-1',ordinary_ids=[4151,26219,27616],exempt_ids=[])
        rows={r['item_id']:r for r in screen(mapping,latest,five,hourly,rules,NOW)}
        self.assertIn('HOURLY_LOW_UNITS_UNKNOWN',rows[4151]['flags'])
        self.assertIn('HOURLY_HIGH_UNITS_UNKNOWN',rows[26219]['flags'])
        for ident in (4151,26219):
            self.assertEqual(rows[ident]['quantity_assumption'],0)
            self.assertIsNone(rows[ident]['conditional_after_tax_gp'])
        self.assertEqual(rows[27616]['status'],'ELIGIBLE_RESEARCH')
        self.assertGreater(rows[27616]['quantity_assumption'],0)

    def test_connected_dose_limit_without_group_registry_is_unqualified(self):
        contract=SourceContract(api_version='v2')
        parsed=lambda kind, body: _parse_safe(kind,json.dumps(body).encode(),NOW,contract)
        mapping=parsed('mapping',SAMPLE['mapping'])['items']
        mapping['999']=dict(name='Prayer potion(4)',members=True,public_limit=2000)
        latest=parsed('latest',SAMPLE['latest']);latest['items']['999']=dict(latest['items']['4151'])
        five=parsed('5m',SAMPLE['5m']);five['items']['999']=dict(five['items']['4151'])
        hour=parsed('1h',SAMPLE['1h']);hour['items']['999']=dict(hour['items']['4151'])
        rules=dict(version='public-rules-2',ordinary_ids=[4151],exempt_ids=[],
                   exempt_registry={},special_exclusions={})
        rows={r['item_id']:r for r in screen(mapping,latest,five,hour,rules,NOW)}
        self.assertIn('SHARED_LIMIT_GROUP_UNREVIEWED',rows[999]['flags'])
        self.assertEqual(rows[999]['status'],'EXCLUDED')
        self.assertNotIn('SHARED_LIMIT_GROUP_UNREVIEWED',rows[4151]['flags'])

    def test_graph_distinguishes_bucket_units_from_at_price_units(self):
        stats=graph_stats([dict(bucket_start=300,low_avg=50.2,high_avg=60.1,low_units=100),
                           dict(bucket_start=600,low_avg=40.1,high_avg=65.2,low_units=5)])
        self.assertEqual(stats['lowest_low_average'],40.1)
        self.assertEqual(stats['low_bucket_units'],5)
        self.assertIsNone(stats['exact_units_at_price'])
        self.assertIsNone(stats['attainable_quantity'])

    def test_learning_fixture_cycle_deterministic_and_time_ordered(self):
        obs=observations();cutoff=max(x['received_at'] for x in obs)
        first=train_once(obs,cutoff);second=train_once(obs,cutoff)
        self.assertEqual(first['status'],'ML_SHADOW')
        self.assertEqual(first['model']['id'],second['model']['id'])
        model=first['model']
        self.assertLess(model['train_last_decision'],model['holdout_first_decision'])
        self.assertGreater(model['samples']['holdout'],0)
        future=copy.deepcopy(obs)
        future.append(dict(obs[0],received_at=cutoff+100,low_avg=999999,capture_hash='later-revision'))
        self.assertEqual(train_once(future,cutoff)['model']['id'],model['id'])

    def test_immature_and_unobserved_labels_excluded(self):
        obs=observations(items=1,buckets=2)
        obs[1]['received_at']=obs[0]['received_at']-1
        self.assertEqual(examples(obs,obs[0]['received_at']),[])
        self.assertEqual(train_once(obs,obs[0]['received_at'])['status'],'DATA_INSUFFICIENT')

    def test_future_hour_target_and_point_in_time_feature_parity(self):
        obs=observations(items=1,buckets=20)
        rows=examples(obs,max(x['received_at'] for x in obs))
        first=rows[0]
        self.assertEqual(HORIZON_SECONDS,3600)
        self.assertEqual(FEATURE_LOOKBACK_SECONDS,300)
        self.assertEqual(first['target_bucket']-first['bucket_start'],3600)
        self.assertGreaterEqual(first['target_bucket']-first['decision_at'],300)
        current=next(o for o in obs if o['bucket_start']==first['bucket_start'])
        prior=next((o for o in obs if o['bucket_start']==first['bucket_start']-300),None)
        self.assertEqual(first['x'],feature_vector(current,prior))
        state=new_state(first['decision_at'])
        state['source_status']='CAPTURED_WITH_REVIEW'
        state['models']=[train_once(observations(),max(o['received_at'] for o in observations()))['model']]
        self.assertEqual(record_predictions(state,[dict(current,previous=prior)],first['decision_at']),1)
        prediction=state['predictions'][0]
        self.assertEqual(prediction['target_bucket'],first['target_bucket'])
        self.assertEqual(prediction['forecast'],predict(state['models'][0],first['x']))
        self.assertEqual(record_predictions(state,[dict(current,previous=prior)],first['target_bucket']-100),0)

    def test_late_label_cannot_enter_earlier_training_slice(self):
        obs=observations();cutoff=max(x['received_at'] for x in obs)
        normal=train_once(obs,cutoff)['model']
        late=copy.deepcopy(obs)
        target=next(x for x in late if x['item_id']==1 and x['bucket_start']==1791000000+10*300)
        target['received_at']=cutoff-1
        changed=train_once(late,cutoff)['model']
        self.assertLess(changed['samples']['train'],normal['samples']['train'])
        self.assertNotEqual(changed['id'],normal['id'])

    def test_schema_failure_opens_circuit_and_keeps_partial_capture(self):
        class Broken(FakeClient):
            def fetch(self,kind,params=None,now=None):
                if kind=='latest': raise ValidationError('Invalid market envelope.')
                return super().fetch(kind,params,now)
        state=new_state(NOW)
        result=collect(state,Broken(NOW),NOW)
        self.assertFalse(result['ok'])
        self.assertEqual(state['source_status'],'PAUSED')
        self.assertGreater(state['circuit_open_until'],NOW)
        self.assertEqual(len(state['capture_metadata']),1)

    def test_bulk_coverage_collapse_pauses_new_advice(self):
        state=new_state(NOW)
        state['route_counts']={'latest':1000}
        result=collect(state,FakeClient(NOW),NOW)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'],'LATEST_COVERAGE_COLLAPSE')
        self.assertEqual(state['source_status'],'DATA_GAP')
        self.assertGreater(state['circuit_open_until'],NOW)

    def test_retry_after_full_wait_and_restart_circuit(self):
        clock=[1000.0]
        slept=[]
        class RateLimited:
            def open(self, request, timeout):
                raise urllib.error.HTTPError(request.full_url,429,'rate limited',
                    {'Retry-After':'120'},None)
        client=SourceClient(SourceContract(api_version='v2'),opener=RateLimited(),
            sleeper=slept.append,clock=lambda:clock[0])
        with self.assertRaises(ProviderDeferred) as captured:
            client.fetch('latest')
        self.assertEqual(captured.exception.retry_at,1120)
        self.assertEqual(client.calls,1)
        self.assertEqual(slept,[])
        state=new_state(1000)
        class Deferred(FakeClient):
            def fetch(self,kind,params=None,now=None):
                self.calls+=1
                raise ProviderDeferred(1120,'HTTP_429_RETRY_DEFERRED')
        first=collect(state,Deferred(1000),1000)
        self.assertEqual(first['error'],'HTTP_429_RETRY_DEFERRED')
        self.assertEqual(state['circuit_open_until'],1120)
        restarted=FakeClient(1060)
        second=collect(state,restarted,1060)
        self.assertEqual(second['error'],'SOURCE_CIRCUIT_OPEN')
        self.assertEqual(restarted.calls,0)

    def test_retry_after_http_date_and_malformed_fallback(self):
        header=format_datetime(datetime.fromtimestamp(1120,timezone.utc),usegmt=True)
        self.assertEqual(_retry_delay('120',0,1000),120)
        self.assertEqual(_retry_delay(header,0,1000),120)
        self.assertEqual(_retry_delay('not-a-date',0,1000),_retry_delay(None,0,1000))

    def test_source_request_and_receipt_use_actual_injected_clock(self):
        clock=[1000.0]
        class Response:
            status=200
            url='https://prices.runescape.wiki/api/v2/osrs/latest'
            def __init__(self):
                self.headers=Message();self.headers['Content-Type']='application/json'
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self,limit): return b'{"data":{}}'
        class Opener:
            def open(self,request,timeout):
                clock[0]=1005.0
                return Response()
        client=SourceClient(SourceContract(api_version='v2'),opener=Opener(),clock=lambda:clock[0])
        _,meta=client.fetch('latest')
        self.assertEqual((meta['requested_at'],meta['received_at']),(1000,1005))

    def test_daily_chart_fetch_budget_is_durable(self):
        now=(NOW//86400)*86400+12*3600+900
        state=new_state(now);state['chart_budget_used']=16
        rules=dict(version='public-rules-1',ordinary_ids=[4151],exempt_ids=[],valid_until='2027-01-01')
        result=cycle(state,FakeClient(now),rules,now,'budget-fixture')
        self.assertEqual(result['source_calls'],5)
        self.assertEqual(state['chart_budget_used'],16)

    def test_chart_retry_after_stops_all_source_requests_and_advice(self):
        now=(NOW//86400)*86400+12*3600+900
        class ChartLimited(FakeClient):
            def fetch(self,kind,params=None,now=None):
                if kind=='timeseries':
                    self.calls+=1
                    raise ProviderDeferred(self.now+120,'HTTP_429_RETRY_DEFERRED')
                return super().fetch(kind,params,now)
        client=ChartLimited(now)
        state=new_state(now)
        rules=dict(version='public-rules-1',ordinary_ids=[4151],exempt_ids=[],valid_until='2027-01-01')
        result=cycle(state,client,rules,now,'chart-rate-limit')
        self.assertEqual(result['status'],'DATA_GAP')
        self.assertEqual(result['issued'],0)
        self.assertEqual(state['circuit_open_until'],now+120)
        self.assertEqual(client.calls,6)

    def test_chart_access_denial_pauses_provider_after_one_request(self):
        now=(NOW//86400)*86400+12*3600+900
        class Denied(FakeClient):
            def fetch(self,kind,params=None,now=None):
                if kind=='timeseries':
                    self.calls+=1
                    raise ValidationError('HTTP_403')
                return super().fetch(kind,params,now)
        client=Denied(now);state=new_state(now)
        rules=dict(version='public-rules-1',ordinary_ids=[4151],exempt_ids=[],valid_until='2027-01-01')
        result=cycle(state,client,rules,now,'chart-access-denied')
        self.assertEqual((result['status'],result['issued']),('PAUSED',0))
        self.assertEqual(client.calls,6)
        self.assertTrue(state['public_pause'])
        self.assertEqual(state['source_error'],'HTTP_403')

    def test_chart_exhausted_network_retry_opens_circuit(self):
        now=(NOW//86400)*86400+12*3600+900
        class Offline(FakeClient):
            def fetch(self,kind,params=None,now=None):
                if kind=='timeseries':
                    self.calls+=1
                    raise ValidationError('TRANSIENT_NETWORK_FAILURE: URLError')
                return super().fetch(kind,params,now)
        client=Offline(now);state=new_state(now)
        rules=dict(version='public-rules-1',ordinary_ids=[4151],exempt_ids=[],valid_until='2027-01-01')
        result=cycle(state,client,rules,now,'chart-network-offline')
        self.assertEqual((result['status'],result['issued']),('DATA_GAP',0))
        self.assertEqual((client.calls,state['circuit_open_until']),(6,now+300))

    def test_review_expiry_during_slow_cycle_blocks_new_advice(self):
        now=(NOW//86400)*86400+12*3600+900
        client=FakeClient(now)
        client.clock=lambda:now+100
        client.contract=replace(client.contract,expires_at=now+50)
        state=new_state(now);state['chart_budget_used']=16
        rules=dict(version='public-rules-1',ordinary_ids=[4151],exempt_ids=[],valid_until='2027-01-01')
        result=cycle(state,client,rules,now,'review-expiry')
        self.assertEqual(result['status'],'PAUSED')
        self.assertEqual(result['issued'],0)
        self.assertEqual(state['source_error'],'SOURCE_CONTRACT_NOT_REVIEWED')
        self.assertEqual(state['last_run_end_at'],now+100)

    def test_no_new_matured_data_and_job_limits(self):
        obs=observations();cutoff=max(x['received_at'] for x in obs)
        self.assertEqual(train_once(obs,cutoff,cutoff-1)['status'],'NO_NEW_MATURED_DATA')
        state=new_state(cutoff);state['observations']=obs;state['last_learning_check']=0
        state['source_status']='CAPTURED_WITH_REVIEW'
        self.assertEqual(daily_readiness(state,cutoff),'ML_SHADOW')
        model_id=state['models'][0]['id']
        self.assertEqual(daily_readiness(state,cutoff+60),'ML_SHADOW')
        self.assertEqual(len(state['models']),1)
        self.assertEqual(model_id,state['models'][0]['id'])

    def test_frozen_holdout_evidence_and_split_metadata_are_checksummed(self):
        obs=observations();model=train_once(obs,max(x['received_at'] for x in obs))['model']
        self.assertTrue(compatible_model(model))
        for field,mutation in [('metrics',lambda m:m['metrics']['holdout'].update(candidate_price_mae_bps=0)),
                               ('samples',lambda m:m['samples'].update(train=1))]:
            altered=copy.deepcopy(model);mutation(altered)
            with self.subTest(field=field):
                self.assertFalse(compatible_model(altered))
                self.assertIn('MODEL_SCHEMA_OR_CHECKSUM',promotion_gate(altered,None,None)['reasons'])
                with self.assertRaises(ValueError):predict(altered,[0,0,0,0])

    def test_verified_deployment_clock_allows_bounded_shadow_replay(self):
        obs=observations();model=train_once(obs,max(x['received_at'] for x in obs))['model']
        model['status']='SHADOW';start=(NOW//3600)*3600
        state=new_state(start);state['models']=[model]
        state['shadow_decisions']=[];state['observations']=[]
        for i in range(100):
            issued=start+i*3600
            plan=dict(item_id=1,buy=100,sell=120,quantity=10,
                      entry_deadline=issued+600,public_limit=10,tax_exempt=False,
                      limit_group_status='VERIFIED_ITEM_ONLY')
            state['shadow_decisions'].append(dict(model_id=model['id'],issued_at=issued,
                public_available_at=issued+30,capital=BANKROLL,slots=4,
                baseline=[plan],candidate=[plan]))
        for bucket in range(start,start+100*3600,300):
            state['observations'].append(dict(item_id=1,bucket_start=bucket,
                received_at=bucket+300,low_avg=100,high_avg=120,low_units=100,
                high_units=100,source='CAPTURED_5M',capture_hash='fixture'))
        result=economic_shadow_comparison(state,start+100*3600+300)
        self.assertEqual(result['evidence_class'],'FORWARD_ASSUMPTION_SIMULATION')
        self.assertEqual(result['clocks'],100)
        self.assertGreater(result['baseline_modeled_sold_units'],0)
        self.assertFalse(promotion_gate(model,None,result)['pass_all'])

    def test_failed_promotion_and_atomic_baseline_rollback(self):
        model=train_once(observations(),max(x['received_at'] for x in observations()))['model']
        failed=promotion_gate(model,None,None)
        self.assertFalse(failed['pass_all'])
        self.assertIn('MATCHED_PORTFOLIO_EVIDENCE',failed['reasons'])
        state=new_state();model['status']='RESEARCH_ACTIVE';state['models']=[model]
        state['active_model_id']=model['id'];rollback(state)
        self.assertIsNone(state['active_model_id'])
        self.assertEqual(state['learning_status'],'RULES_BASELINE')

    def test_research_policy_is_versioned_and_promotion_is_reversible(self):
        now=NOW;state=new_state(now);state['source_status']='CAPTURED_WITH_REVIEW'
        row=valid_row(1,now);row['ranking_mode']='ML_RESEARCH_ACTIVE'
        research=issue_picks(state,[row],now,[dict(sha256='abc')])[0]
        self.assertEqual(research['policy'],'ml-research-rank-1')
        baseline_state=new_state(now);baseline_state['source_status']='CAPTURED_WITH_REVIEW'
        baseline=issue_picks(baseline_state,[valid_row(1,now)],now,[dict(sha256='abc')])[0]
        self.assertEqual(baseline['policy'],'rules-baseline-1')
        self.assertNotEqual(research['id'],baseline['id'])
        obs=observations();cutoff=max(o['received_at'] for o in obs)
        model=train_once(obs,cutoff)['model'];model['status']='SHADOW'
        promotion=new_state(cutoff);promotion['source_status']='CAPTURED_WITH_REVIEW'
        promotion['models']=[model];promotion['last_learning_check']=cutoff
        with (patch('learning.shadow_metrics',return_value={model['id']:{'n':400,'days':7,'coverage_bps':9500}}),
              patch('learning.shadow_portfolio_comparison',return_value={'clocks':100}),
              patch('learning.economic_shadow_comparison',return_value={'evidence_class':'FORWARD_ASSUMPTION_SIMULATION'}),
              patch('learning.promotion_gate',return_value={'pass_all':True,'reasons':[]})):
            self.assertEqual(daily_readiness(promotion,cutoff+86400),'ML_RESEARCH_ACTIVE')
        self.assertEqual(promotion['active_model_id'],model['id'])
        self.assertEqual(model['status'],'RESEARCH_ACTIVE')
        rollback(promotion)
        self.assertIsNone(promotion['active_model_id'])
        self.assertEqual(promotion['learning_status'],'RULES_BASELINE')

    def test_two_bounded_generations_need_new_matured_clocks(self):
        obs=observations();cutoff=max(o['received_at'] for o in obs)
        state=new_state(cutoff);state['observations']=obs;state['source_status']='CAPTURED_WITH_REVIEW'
        self.assertEqual(daily_readiness(state,cutoff),'ML_SHADOW')
        first_id=state['models'][0]['id']
        good_shadow=dict(n=400,days=7,coverage_bps=9500,baseline_price_mae_bps=100,
            candidate_price_mae_bps=80,baseline_log_units_mae=1,candidate_log_units_mae=.8,
            baseline_high_price_mae_bps=100,candidate_high_price_mae_bps=80,
            baseline_high_log_units_mae=1,candidate_high_log_units_mae=.8,positive_days=7)
        reference=dict(evidence_class='REFERENCE_MARK_NO_FILLS',clocks=100,coverage_bps=9500)
        with (patch('learning.shadow_metrics',return_value={first_id:good_shadow}),
              patch('learning.shadow_portfolio_comparison',return_value=reference)):
            self.assertEqual(daily_readiness(state,cutoff+86400),'RULES_BASELINE')
        self.assertEqual(state['models'][0]['status'],'SHADOW_REJECTED')
        self.assertIn('MATCHED_PORTFOLIO_EVIDENCE',state['promotion_gate']['reasons'])
        state['observations']=observations(buckets=500)
        second_time=max(o['received_at'] for o in state['observations'])
        self.assertEqual(daily_readiness(state,second_time+2*86400),'ML_SHADOW')
        self.assertEqual(len(state['models']),2)
        self.assertNotEqual(first_id,state['models'][1]['id'])
        self.assertEqual(state['models'][1]['generation'],2)
        self.assertNotEqual(state['models'][0]['lambda_'],state['models'][1]['lambda_'])

    def test_corrupt_model_checksum_is_rejected_before_prediction(self):
        obs=observations();cutoff=max(o['received_at'] for o in obs)
        model=train_once(obs,cutoff)['model']
        self.assertTrue(compatible_model(model))
        model['price_weights'][0]+=100
        self.assertFalse(compatible_model(model))
        with self.assertRaises(ValueError): predict(model,[0,1,1,0])

    def test_paper_cash_conservation_and_expiry(self):
        now=NOW;state=new_state(now);state['source_status']='CAPTURED_WITH_REVIEW'
        rows=[valid_row(i,now,qty=100,buy=1_000_000,sell=1_100_000) for i in range(1,8)]
        issued=issue_picks(state,rows,now,[dict(sha256='abc')])
        self.assertEqual(len(issued),4)
        self.assertEqual(state['paper_cash']+state['paper_reserved'],BANKROLL)
        self.assertEqual(state['paper_reserved'],sum(p['required_cash'] for p in issued))
        self.assertEqual(issue_picks(state,rows,now,[dict(sha256='abc')]),[])
        issue_picks(state,rows,now+300,[dict(sha256='def')])
        self.assertEqual(state['paper_cash']+state['paper_reserved'],BANKROLL)
        self.assertTrue(any(p['status']=='WINDOW_EXPIRED_EXECUTION_UNKNOWN' for p in state['picks']))

    def test_public_export_recursive_private_canary(self):
        state=new_state(NOW)
        canary='PRIVATE_ACCOUNT_SECRET_CANARY'
        state['recorded_account_state']={'rsn':canary,'account_hash':canary}
        state['local_path']=canary
        with self.assertRaises(ValueError): public_export(state,NOW)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError): save_state(Path(tmp)/'state.json',state)
        del state['recorded_account_state'];del state['local_path']
        state['history_errors']={'4151':{'rsn':canary}}
        with self.assertRaises(ValueError): public_export(state,NOW)
        del state['history_errors']
        self.assertEqual(public_export(state,NOW)['source_status'],'DATA_UNAVAILABLE')

    def test_publication_lag_does_not_create_fill(self):
        state=new_state(NOW);state['source_status']='CAPTURED_WITH_REVIEW'
        pick=issue_picks(state,[valid_row(1,NOW)],NOW,[dict(sha256='abc')])[0]
        self.assertEqual(grade_picks(state,[],NOW+7200),0)
        self.assertEqual(mark_published(state,{pick['id']},NOW+10,'deploy-1','https://example.test'),1)
        self.assertEqual(mark_published(state,{pick['id']},NOW+10,'deploy-1','https://example.test'),0)
        self.assertEqual(grade_picks(state,[],NOW+8000),1)
        self.assertEqual(state['outcomes'][0]['status'],'DATA_GAP')
        self.assertIsNone(state['outcomes'][0]['actual_gp'])
        self.assertIsNone(state['outcomes'][0]['hypothetical_fill'])

    def test_failed_ack_recovery_requires_successful_prior_deploy(self):
        state=new_state(NOW);state['source_status']='CAPTURED_WITH_REVIEW'
        pick=issue_picks(state,[valid_row(1,NOW)],NOW,[dict(sha256='abc')])[0]
        state['pending_publication']=dict(deployment_id='123-1',artifact_id='a'*32,
            export_sha256='b'*64,pick_ids=[pick['id']],run_event='schedule')
        with self.assertRaises(ValueError): recover_pending_publication(state,[],NOW+30,'https://example.test')
        self.assertIsNone(pick['first_public_at'])
        recovered=recover_pending_publication(state,[dict(name='deploy',status='completed',
            conclusion='success',completed_at=datetime.fromtimestamp(NOW+20,timezone.utc).isoformat())],
            NOW+3600,'https://example.test')
        self.assertEqual(recovered['published'],1)
        self.assertEqual(pick['first_public_at'],NOW+20)
        self.assertEqual(pick['status'],'PUBLIC_FORWARD_PAPER')
        self.assertIsNone(state['pending_publication'])
        self.assertEqual(state['last_automatic_publication_at'],NOW+20)
        self.assertEqual(state['publication_receipts'][-1]['artifact_id'],'a'*32)
        self.assertEqual(state['publication_receipts'][-1]['recovered_at_next_run'],NOW+3600)
        self.assertEqual(state['publication_receipts'][-1]['availability_clock'],'VERIFIED_DEPLOY_JOB_COMPLETION')
        self.assertEqual(recover_pending_publication(state,[],NOW+120,'https://example.test')['status'],
                         'NO_PENDING_DEPLOYMENT')
        failed=new_state(NOW);failed['pending_publication']=dict(deployment_id='124-1',
            artifact_id='a'*32,export_sha256='b'*64,pick_ids=[],run_event='push')
        self.assertEqual(recover_pending_publication(failed,[dict(name='deploy',status='completed',
            conclusion='failure')],NOW+60,'https://example.test')['status'],'PREVIOUS_DEPLOYMENT_FAILED')
        self.assertEqual(failed['publication_receipts'],[])

    def test_known_backfill_gap_is_visible_without_making_old_quotes_fresh(self):
        state=new_state(NOW)
        state['data_gap']=dict(from_bucket=NOW-7200,to_bucket=NOW-3600,reason='BACKFILL_BOUND')
        export=public_export(state,NOW+60)
        self.assertEqual(export['known_data_gap'],state['data_gap'])
        self.assertEqual(export['source_status'],'DATA_UNAVAILABLE')

    def test_verified_deploy_timestamp_is_required_and_bounded(self):
        good=dict(name='deploy',status='completed',conclusion='success',
                  completed_at=datetime.fromtimestamp(NOW+10,timezone.utc).isoformat())
        self.assertEqual(verified_deploy_completed_at([good],NOW+20),NOW+10)
        for bad in ({**good,'completed_at':None}, {**good,'completed_at':'2026-01-01'},
                    {**good,'completed_at':datetime.fromtimestamp(NOW+90,timezone.utc).isoformat()},
                    {**good,'status':'in_progress'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                verified_deploy_completed_at([bad],NOW+20)

    def test_late_price_path_revision_preserves_gap_and_loss_mark(self):
        state=new_state(NOW);state['source_status']='CAPTURED_WITH_REVIEW'
        pick=issue_picks(state,[valid_row(1,NOW)],NOW,[dict(sha256='abc')])[0]
        mark_published(state,{pick['id']},NOW+10,'deploy-2','https://example.test')
        grade_picks(state,[],NOW+8000)
        bucket=((NOW+3900)//300)*300
        later=dict(item_id=1,bucket_start=bucket,low_avg=900_000,high_avg=950_000,
                   low_units=4,high_units=8,received_at=NOW+8100,capture_hash='late',source='CAPTURED_5M')
        self.assertEqual(grade_picks(state,[later],NOW+8200),1)
        self.assertEqual([o['status'] for o in state['outcomes']],['DATA_GAP','PRICE_PATH_OBSERVED'])
        self.assertEqual(state['outcomes'][1]['parent_id'],state['outcomes'][0]['id'])
        self.assertLess(state['outcomes'][1]['reference_mark_gp'],0)
        self.assertIsNone(state['outcomes'][1]['actual_gp'])

    def test_state_missing_fails_closed_and_archive_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json'
            with self.assertRaises(ValueError): load_state(path)
            state=new_state(NOW);save_state(path,state)
            self.assertEqual(load_state(path)['schema'],state['schema'])
            state['picks']=[dict(id=str(i),item_id=i,issued_at=NOW-90000,
                first_public_at=None,status='ISSUED_NOT_YET_PUBLIC') for i in range(121)]
            state['picks'][0]['first_public_at']=NOW-89900
            state['picks'][0]['status']='WINDOW_EXPIRED_EXECUTION_UNKNOWN'
            state['outcomes']=[dict(id='gap-0',pick_id='0',status='DATA_GAP')]
            archive=Path(tmp)/'archive'
            compact_history(state,archive,NOW)
            self.assertEqual(state['archived_counts']['picks'],1)
            self.assertEqual(state['archived_counts']['outcomes'],1)
            with gzip.open(next(archive.glob('*.gz')),'rt') as f: self.assertEqual(len(f.readlines()),2)
            compact_history(state,archive,NOW)
            self.assertEqual(state['archived_counts']['picks'],1)

    def test_end_to_end_fixture_cycle_and_idempotency(self):
        now=(NOW//86400)*86400+12*3600+900
        state=new_state(now);client=FakeClient(now)
        rules=dict(version='public-rules-1',ordinary_ids=[4151],exempt_ids=[],valid_until='2027-01-01')
        first=cycle(state,client,rules,now,'fixture-run')
        self.assertEqual(first['status'],'CAPTURED_WITH_REVIEW')
        self.assertEqual(first['issued'],1)
        self.assertEqual(cycle(state,client,rules,now,'fixture-run')['status'],'IDEMPOTENT_REPLAY')
        self.assertEqual(len(state['picks']),1)
        self.assertEqual(state['paper_cash']+state['paper_reserved'],BANKROLL)
        self.assertEqual(state['learning_status'],'DATA_INSUFFICIENT')


if __name__=='__main__': unittest.main()
