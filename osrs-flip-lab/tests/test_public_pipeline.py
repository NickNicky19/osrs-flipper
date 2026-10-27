"""Public-only pipeline regressions. Captured fixture is a reduced real v2 body."""
import copy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error

from market import SourceContract, parse_payload
from core import ValidationError
from public_pipeline import (BANKROLL, SourceClient, _parse_safe, cycle, grade_picks,
    graph_stats, issue_picks, load_state, mark_published, new_state, parse_timeseries,
    public_export, save_state, screen, compact_history, collect)
from learning import (MIN_EXAMPLES, daily_readiness, examples, promotion_gate,
    rollback, shadow_metrics, train_once)


SAMPLE = json.loads((Path(__file__).parent/'fixtures'/'captured-v2-sample.json').read_text(encoding='utf-8'))
NOW = 1793132577


def observations(items=6, buckets=85, start=1791000000):
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

    def test_daily_chart_fetch_budget_is_durable(self):
        now=(NOW//86400)*86400+12*3600+900
        state=new_state(now);state['chart_budget_used']=16
        rules=dict(version='public-rules-1',ordinary_ids=[4151],exempt_ids=[],valid_until='2027-01-01')
        result=cycle(state,FakeClient(now),rules,now,'budget-fixture')
        self.assertEqual(result['source_calls'],5)
        self.assertEqual(state['chart_budget_used'],16)

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

    def test_failed_promotion_and_atomic_baseline_rollback(self):
        model=train_once(observations(),max(x['received_at'] for x in observations()))['model']
        failed=promotion_gate(model,None,None)
        self.assertFalse(failed['pass_all'])
        self.assertIn('MATCHED_PORTFOLIO_EVIDENCE',failed['reasons'])
        state=new_state();model['status']='RESEARCH_ACTIVE';state['models']=[model]
        state['active_model_id']=model['id'];rollback(state)
        self.assertIsNone(state['active_model_id'])
        self.assertEqual(state['learning_status'],'RULES_BASELINE')

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
            archive=Path(tmp)/'archive'
            compact_history(state,archive,NOW)
            self.assertEqual(state['archived_counts']['picks'],1)
            with gzip.open(next(archive.glob('*.gz')),'rt') as f: self.assertEqual(len(f.readlines()),1)
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
