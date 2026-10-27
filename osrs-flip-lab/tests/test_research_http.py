import http.client
import json
import re
import tempfile
import threading
from pathlib import Path
import unittest
from app import make_server,ROOT
from core import Ledger
from service import ResearchService

class ResearchHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.ledger=Ledger(self.path/'demo.sqlite3','demo');self.svc=ResearchService(self.ledger,ROOT)
        self.server=make_server(self.ledger,0,self.svc);self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        _,html=self.request('/');self.token=re.search(b'name="local-token" content="([^"]+)',html)[1].decode()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2);self.temp.cleanup()
    def request(self,path,method='GET',body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        c.request(method,path,body,headers or {});r=c.getresponse();status=r.status;out=r.read();c.close();return status,out
    def post(self,path,body):
        return self.request(path,'POST',json.dumps(body),{'Content-Type':'application/json','X-Local-Token':self.token})
    def report(self):return json.loads(self.request('/api/research')[1])
    def test_report_real_http(self):
        r=self.report();self.assertEqual(len(r['rows']),6);self.assertEqual(r['namespace'],'fixture')
    def test_research_js_is_served(self):
        status,body=self.request('/research.js');self.assertEqual(status,200);self.assertIn(b'refreshResearch',body)
    def test_invalid_window_is_rejected(self):self.assertEqual(self.request('/api/research?window=1')[0],400)
    def test_scenario_and_freeze_through_http(self):
        r=self.report();body=dict(report_hash=r['report_hash'],item_id=900101,buy=10000,sell=10400,quantity=100)
        status,out=self.post('/api/scenario',body);self.assertEqual(status,200);self.assertEqual(json.loads(out)['conditional_net'],19200)
        body['id']='decision-test';self.assertEqual(self.post('/api/research/freeze',body)[0],200)
        self.assertEqual(len(self.ledger.get()['offers']),0)
    def test_new_routes_require_token(self):
        for p in ('/api/scenario','/api/research/freeze'):
            self.assertEqual(self.request(p,'POST','{}',{'Content-Type':'application/json'})[0],403)
    def test_nonfinite_json_rejected(self):
        self.assertEqual(self.request('/api/scenario','POST','{"quantity":NaN}',{'Content-Type':'application/json','X-Local-Token':self.token})[0],400)
    def test_duplicate_json_keys_rejected(self):
        self.assertEqual(self.request('/api/scenario','POST','{"quantity":1,"quantity":2}',{'Content-Type':'application/json','X-Local-Token':self.token})[0],400)
    def test_export_has_capture_provenance(self):
        status,out=self.request('/api/market/export');self.assertEqual(status,200)
        data=json.loads(out);self.assertEqual(len(data['captures']),14);self.assertEqual(data['namespace'],'fixture')
    def test_evidence_does_not_claim_performance(self):
        r=json.loads(self.request('/api/evidence')[1]);self.assertEqual(r['evidence_status'],'NO_PROSPECTIVE_POLICY_COMPARISON')
    def test_public_journal_has_no_fixture_fallback(self):
        ledger=Ledger(self.path/'real.sqlite3','journal')
        svc=ResearchService(ledger,ROOT)
        r=svc.report();self.assertEqual(r['namespace'],'public');self.assertEqual(r['rows'],[])
        self.assertEqual(r['source_status'],'BLOCKED_OR_UNREVIEWED')
    def test_corrupt_market_not_hidden(self):
        with self.svc.store.connect() as db:db.execute("UPDATE captures SET digest='bad' WHERE kind='latest'")
        status,out=self.request('/api/research');self.assertEqual(status,400);self.assertIn(b'integrity',out)
