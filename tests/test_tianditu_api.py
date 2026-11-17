"""TianDiTu HTTP integration boundaries in an isolated workspace (no live key)."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,urlopen

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.server import Workspace, LocalServer, make_handler

TEST_KEY = '0123456789abcdef0123456789abcdef'

class TiandituAPITest(unittest.TestCase):
    def setUp(self):
        self.environment=patch.dict(os.environ,{},clear=False)
        self.environment.start()
        os.environ.pop('TIANDITU_TOKEN',None)
        self.temp=tempfile.TemporaryDirectory()
        self.workspace=Workspace(Path(self.temp.name)/'workspace.sqlite3',node_delay=.001)
        self.server=LocalServer(('127.0.0.1',0),make_handler(self.workspace))
        self.server.quiet=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.workspace.close();self.server.shutdown();self.server.server_close()
        self.thread.join(2);self.temp.cleanup();self.environment.stop()
    def request(self,path,body=None,headers=None):
        head=dict(headers or {})
        if body is not None:head['Content-Type']='application/json'
        req=Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=head)
        try:res=urlopen(req,timeout=5)
        except HTTPError as e:res=e
        content=res.read()
        return res.status,json.loads(content) if 'json' in res.headers.get('Content-Type','') else content
    def test_unconfigured_is_explicit_and_existing_map_remains_usable(self):
        status,data=self.request('/api/integrations/tianditu')
        self.assertEqual(status,200);self.assertFalse(data['configured'])
        self.assertEqual(data['status'],'not_configured')
        status,config=self.request('/api/map/config')
        self.assertEqual(status,200)
        national=[b for b in config['basemaps'] if b.get('provider')=='tianditu']
        self.assertEqual(len(national),2)
        self.assertTrue(all(not b['available'] for b in national))
        self.assertEqual(config['basemaps'][0]['id'],'imagery')
        for b in national:
            self.assertTrue(b['browser_url'].startswith('/api/tianditu/tiles/'))
            self.assertTrue(b['annotation_url'].startswith('/api/tianditu/tiles/'))
            self.assertNotIn('tk=',json.dumps(b))
        status,hit=self.request('/api/map/search?q=114.2,30.5&remote=0')
        self.assertEqual(status,200);self.assertEqual(hit['results'][0]['kind'],'coordinate')
    def test_config_roundtrip_is_private_and_clear_preserves_business_records(self):
        with self.workspace.connection() as conn:before=conn.execute('SELECT count(*) FROM records').fetchone()[0]
        status,data=self.request('/api/integrations/tianditu',{'token':TEST_KEY})
        self.assertEqual(status,200);self.assertTrue(data['configured']);self.assertEqual(data['status'],'configured')
        self.assertNotIn(TEST_KEY,json.dumps(data))
        file=Path(self.temp.name)/'integrations/tianditu.json'
        self.assertEqual(file.stat().st_mode&0o777,0o600)
        for path in ('/api/integrations/tianditu','/api/map/config','/api/health','/api/data-sources','/api/bootstrap'):
            status,result=self.request(path)
            self.assertEqual(status,200,path);self.assertNotIn(TEST_KEY,json.dumps(result))
        status,result=self.request('/api/integrations/tianditu',{'action':'clear'})
        self.assertEqual(status,200);self.assertFalse(result['configured']);self.assertFalse(file.exists())
        with self.workspace.connection() as conn:after=conn.execute('SELECT count(*) FROM records').fetchone()[0]
        self.assertEqual(before,after)
    def test_missing_key_never_returns_fake_tile_address_or_boundary(self):
        for path in ('/api/tianditu/tiles/img/10/838/421','/api/tianditu/reverse?lon=114.2&lat=30.5','/api/tianditu/administrative?keyword=420000'):
            status,result=self.request(path)
            self.assertEqual(status,503,path);self.assertEqual(result['error']['code'],'TIANDITU_NOT_CONFIGURED')
            self.assertNotIn('features',result)
    def test_configuration_origin_and_fields_are_checked(self):
        status,_=self.request('/api/integrations/tianditu',{'token':TEST_KEY},{'Origin':'https://other.invalid'})
        self.assertEqual(status,403)
        for body in ({'token':'invalid'},{'token':TEST_KEY,'url':'https://other.invalid'},{'action':'clear','token':TEST_KEY}):
            status,data=self.request('/api/integrations/tianditu',body)
            self.assertEqual(status,400);self.assertNotIn(TEST_KEY,json.dumps(data))
        status,_=self.request('/api/integrations/tianditu/check',{'url':'https://other.invalid'})
        self.assertEqual(status,400)
        status,_=self.request('/api/map/search?q=test&remote=yes')
        self.assertEqual(status,400)
    def test_catalog_separates_map_reference_from_analysis_pixels(self):
        status,data=self.request('/api/data-sources')
        self.assertEqual(status,200)
        national=[s for s in data['sources'] if s['id'].startswith('tianditu_')]
        self.assertEqual(len(national),3)
        for source in national:
            self.assertTrue(source['auth']['user_key_required'])
            self.assertEqual(source['stages']['connection_status'],'not_configured')
            self.assertFalse(source['stages']['catalog_searchable'])
            self.assertFalse(source['stages']['pixel_readable'])
            self.assertFalse(source['stages']['analysis_ready'])
            self.assertTrue(source['stages']['service_adapter_implemented'])

if __name__=='__main__':unittest.main()
