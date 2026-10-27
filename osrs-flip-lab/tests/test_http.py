import http.client
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from app import make_server
from core import Ledger


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.ledger=Ledger(Path(self.temp.name)/'demo.sqlite3','demo')
        self.server=make_server(self.ledger,0)
        self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2);self.temp.cleanup()

    def request(self,path,method='GET',body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        c.request(method,path,body=body,headers=headers or {})
        response=c.getresponse();status=response.status;data=response.read();c.close();return status,data

    def test_site_and_fixture_label(self):
        status,html=self.request('/')
        self.assertEqual(status,200)
        self.assertIn(b'local-token',html)
        status,body=self.request('/api/state')
        result=json.loads(body)
        self.assertEqual(result['source_status'],'FIXTURE_ONLY')
        self.assertEqual(len(result['opportunities']),4)

    def test_unknown_host_rejected(self):
        status,_=self.request('/api/state',headers={'Host':'evil.example'})
        self.assertEqual(status,403)

    def test_missing_token_blocks_mutation(self):
        status,_=self.request('/api/event','POST','{}',{'Content-Type':'application/json'})
        self.assertEqual(status,403)

    def test_cross_origin_blocks_mutation(self):
        _,html=self.request('/')
        token=re.search(rb'name="local-token" content="([^"]+)"',html).group(1).decode()
        status,_=self.request('/api/event','POST','{}',{'Content-Type':'application/json','X-Local-Token':token,'Origin':'https://evil.example'})
        self.assertEqual(status,403)

    def test_valid_post_and_persistent_export(self):
        _,html=self.request('/')
        token=re.search(rb'name="local-token" content="([^"]+)"',html).group(1).decode()
        event={'id':'test1','type':'place','side':'BUY','item_id':1,'name':'Test','quantity':1,'price':100,'allowance_checked':True}
        status,_=self.request('/api/event','POST',json.dumps(event),{'Content-Type':'application/json','X-Local-Token':token})
        self.assertEqual(status,200)
        _,body=self.request('/api/export');backup=json.loads(body)
        self.assertEqual(len(backup['events']),1)
        self.assertEqual(backup['state']['free_cash'],499999900)

    def test_static_path_not_arbitrary_files(self):
        status,_=self.request('/../../core.py')
        self.assertEqual(status,404)

    def test_malformed_event_id_is_400_not_server_error(self):
        _,html=self.request('/')
        token=re.search(rb'name="local-token" content="([^"]+)"',html).group(1).decode()
        event={'id': [], 'type': 'place'}
        status,_=self.request('/api/event','POST',json.dumps(event),{'Content-Type':'application/json','X-Local-Token':token})
        self.assertEqual(status,400)
        self.assertEqual(self.ledger.export()['events'],[])

    def test_factual_record_warning_is_returned_to_ui(self):
        _,html=self.request('/')
        token=re.search(rb'name="local-token" content="([^"]+)"',html).group(1).decode()
        event={'id':'actual','type':'place','side':'BUY','item_id':1,'name':'Test','quantity':6000,'price':10000,
               'allowance_checked':False,'record_external_action':True,
               'exception_reason':'Already manually placed; record actual state without approval.'}
        status,body=self.request('/api/event','POST',json.dumps(event),{'Content-Type':'application/json','X-Local-Token':token})
        self.assertEqual(status,200)
        reply=json.loads(body)
        self.assertTrue(reply['summary']['exposure_over_cap'])
        self.assertEqual(len(reply['summary']['recording_warnings']),1)
        self.assertEqual(reply['state']['exposure_cap'],50000000)


if __name__=='__main__':unittest.main()
