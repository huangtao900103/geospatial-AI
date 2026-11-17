"""HTTP integration: real inputs, empty states and retired synthetic entrypoints."""
import json
from pathlib import Path
import sys,tempfile,threading,unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from urllib.parse import quote
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.server import Workspace,LocalServer,make_handler,MAX_BODY,APIError

def user_geo():return {'type':'FeatureCollection','features':[{'type':'Feature','id':'user-point','properties':{'name':'Provided location'},'geometry':{'type':'Point','coordinates':[113.4,30.3]}}]}

class APITest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.db=str(Path(self.temp.name)/'test.sqlite3');self.workspace=Workspace(self.db,node_delay=.025);self.server=LocalServer(('127.0.0.1',0),make_handler(self.workspace));self.server.quiet=True;self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.server.server_port)
 def tearDown(self):self.workspace.close();self.server.shutdown();self.server.server_close();self.thread.join(2);self.temp.cleanup()
 def request(self,method,path,body=None,headers=None,raw=None):
  headers=dict(headers or {});data=raw
  if body is not None:data=json.dumps(body).encode();headers.setdefault('Content-Type','application/json')
  try:response=urlopen(Request(self.base+quote(path,safe='/?:=&,%'),data=data,headers=headers,method=method),timeout=5)
  except HTTPError as error:response=error
  payload=response.read();return response.status,json.loads(payload) if 'application/json' in response.headers.get('Content-Type','') else payload
 def test_bootstrap_and_map_start_empty_without_virtual_seed(self):
  code,boot=self.request('GET','/api/bootstrap');self.assertEqual(code,200);self.assertEqual(len(boot['scenarios']),8);self.assertEqual(boot['datasets'],[]);self.assertEqual(boot['metrics']['datasets'],0);self.assertEqual(boot['metrics']['workflows'],0);self.assertFalse(any(r['id']=='xt-fixture' for r in boot['resources']))
  for path in ('/api/map','/api/map?scenario=crop','/api/map?region=武汉市'):
   code,geo=self.request('GET',path);self.assertEqual(code,200);self.assertEqual(geo['type'],'FeatureCollection');self.assertEqual(geo['features'],[]);self.assertEqual(geo['metadata']['mode'],'empty_workspace')
 def test_virtual_fixture_and_synthetic_layer_old_urls_are_gone(self):
  for path in ('/api/datasets/xt-fixture','/api/map/layers/agriculture','/api/map/layers/industry','/api/map/layers/machinery','/api/map/layers/water','/api/map/layers/alerts'):
   code,result=self.request('GET',path);self.assertEqual(code,410,result);self.assertIn('REMOVED',result['error']['code'])
  code,_=self.request('POST','/api/analysis',{'dataset_id':'xt-fixture','algorithm_id':'spatial_summary','parameters':{}});self.assertEqual(code,410);self.assertFalse(self.workspace.list('analysis'))
 def test_full_multitheme_request_cannot_generate_fixture_plan_or_run(self):
  request={'requirement':'底数、高标准农田、尾水治理、粮食保护、秋冬播遥感、防灾减灾、长江禁捕、黄鳝产业链、北斗农机、冷链物流、土地确权、水利平台对接、小程序填报'}
  code,answer=self.request('POST','/api/plans',request);self.assertEqual(code,410);self.assertEqual(answer['error']['code'],'LEGACY_SAMPLE_WORKFLOW_REMOVED')
  code,answer=self.request('POST','/api/runs',{'plan_id':'old_plan'});self.assertEqual(code,410);self.assertEqual(answer['error']['code'],'LEGACY_SAMPLE_WORKFLOW_REMOVED');self.assertFalse(self.workspace.list('plan'));self.assertFalse(self.workspace.list('run'))
 def test_removed_workflow_never_falls_back_for_unknown_goal_or_region(self):
  for body in ({'requirement':'hello'},{'requirement':'统计农田','region':'武汉市'},{'requirement':'统计示例','region':'仙桃市'}):
   code,result=self.request('POST','/api/plans',body);self.assertEqual(code,410,result)
  self.assertFalse(self.workspace.list('plan'))
 def test_legacy_app_creation_update_publish_clone_are_disabled(self):
  for method,path,body in [('POST','/api/apps',{'name':'农业应用','modules':['baseline'],'layer_ids':[]} ),('PATCH','/api/apps/old_app',{'name':'changed'}),('POST','/api/apps/old_app/publish',{'scope':'workspace'}),('POST','/api/apps/old_app/clone',{})]:
   code,result=self.request(method,path,body);self.assertEqual(code,410,result);self.assertEqual(result['error']['code'],'LEGACY_SAMPLE_APP_REMOVED')
  self.assertFalse(self.workspace.list('app'))
 def test_real_import_and_analysis_preserve_input_and_use_no_virtual_layer(self):
  code,dataset=self.request('POST','/api/datasets',{'name':'User-provided location','geojson':user_geo()});self.assertEqual(code,201);self.assertEqual(dataset['source_type'],'user')
  code,full=self.request('GET','/api/datasets/'+dataset['id']);self.assertEqual(code,200);self.assertEqual(full['geojson'],user_geo())
  code,result=self.request('POST','/api/analysis',{'dataset_id':dataset['id'],'algorithm_id':'spatial_summary','parameters':{}});self.assertEqual(code,201);self.assertEqual(result['feature_count'],1);self.assertEqual(result['provenance']['mode'],'user-data-analysis');self.assertEqual(result['provenance']['dataset_id'],dataset['id'])
  _,boot=self.request('GET','/api/bootstrap');self.assertEqual([d['id'] for d in boot['datasets']],[dataset['id']]);_,geo=self.request('GET','/api/map');self.assertEqual(geo['features'],[])
 def test_input_validation_and_raw_text_storage_still_apply_to_real_user_data(self):
  raw_name='<img src=x onerror=alert(1)>'
  code,dataset=self.request('POST','/api/datasets',{'name':raw_name,'geojson':user_geo()});self.assertEqual(code,201);self.assertEqual(dataset['name'],raw_name)
  for body in ({'name':5,'geojson':user_geo()},{'name':'x'*101,'geojson':user_geo()},{'name':'bad','geojson':user_geo(),'source_type':'synthetic'}):
   code,_=self.request('POST','/api/datasets',body);self.assertEqual(code,400)
 def test_endpoint_drafts_never_accept_credentials(self):
  for endpoint in ['ftp://example.com/data','https://user:pw@example.com','https://example.com/?token=secret','javascript:alert(1)']:
   code,_=self.request('POST','/api/integrations/remote-sensing',{'endpoint':endpoint,'protocol':'REST API'});self.assertEqual(code,400,endpoint)
  code,draft=self.request('POST','/api/integrations/remote-sensing',{'endpoint':'https://example.com/api','protocol':'REST API'});self.assertEqual(code,200);self.assertFalse(draft['verified']);_,boot=self.request('GET','/api/bootstrap');self.assertEqual(boot['integrations'][0]['endpoint'],'https://example.com/api')
 def test_host_origin_json_size_and_static_boundaries(self):
  code,_=self.request('GET','/api/health',headers={'Host':'evil.example'});self.assertEqual(code,403)
  code,_=self.request('POST','/api/plans',{'requirement':'秋冬播'},headers={'Origin':'https://evil.example'});self.assertEqual(code,403)
  code,_=self.request('POST','/api/plans',raw=b'{}');self.assertEqual(code,415)
  code,_=self.request('POST','/api/plans',raw=b'{broken',headers={'Content-Type':'application/json'});self.assertEqual(code,400)
  code,_=self.request('POST','/api/plans',raw=b'x'*(MAX_BODY+1),headers={'Content-Type':'application/json'});self.assertEqual(code,413)
  for path in ['/../server.py','/%2e%2e/server.py','/data/workspace.sqlite3','/.git/config']:
   code,_=self.request('GET',path);self.assertEqual(code,404,path)
  code,_=self.request('GET','/api/missing');self.assertEqual(code,404)
 def test_startup_never_invokes_the_pure_test_fixture_generator(self):
  with patch('backend.server.fixture',side_effect=AssertionError('Production must not seed samples')):
   reopened=Workspace(self.db,node_delay=.01)
   try:self.assertEqual(reopened.datasets(),[]);self.assertEqual(reopened.map_data()['features'],[])
   finally:reopened.close()
 def test_real_data_persists_and_old_inflight_sample_cannot_resume_or_retry(self):
  dataset=self.workspace.create_dataset({'name':'Persistent user data','geojson':user_geo()});pending={'id':'run_interrupted','plan_id':'retired_plan','name':'Archived legacy sample','status':'running','progress':20,'nodes':[{'id':'node','status':'running'}],'logs':[],'result':None,'created_at':'2026-01-01'};self.workspace.save('run',pending)
  reopened=Workspace(self.db,node_delay=.01)
  try:
   self.assertEqual(reopened.get_dataset(dataset['id'])['sha256'],dataset['sha256']);old=reopened.get('run',pending['id']);self.assertEqual(old['status'],'failed');self.assertIsNone(old['result']);self.assertIn('中断',old['error'])
   with self.assertRaises(APIError) as error:reopened.retry(old['id'])
   self.assertEqual(error.exception.status,410);self.assertEqual(len(reopened.list('run')),1)
  finally:reopened.close()

 def test_public_production_never_uses_implicit_sample_extent_or_date(self):
  for body,code,error in [({'requirement':'分析NDVI','mode':'sample_pixels'},410,'SAMPLE_MODE_REMOVED'),({'requirement':'分析2025年11月NDVI','mode':'sentinel_cog','sampling':'native'},422,'AOI_REQUIRED'),({'requirement':'分析选区NDVI','mode':'sentinel_cog','sampling':'native','bbox':[114.2,30.5,114.21,30.51]},422,'PERIOD_REQUIRED')]:
   with self.subTest(error=error):
    status,result=self.request('POST','/api/production/tasks',body);self.assertEqual(status,code,result);self.assertEqual(result['error']['code'],error)
  self.assertEqual(self.workspace.list('production_task'),[])
  status,cap=self.request('GET','/api/production/capabilities');self.assertEqual(status,200);self.assertEqual([m['id'] for m in cap['modes']],['sentinel_cog']);self.assertIsNone(cap['default_bbox'])

if __name__=='__main__':unittest.main(verbosity=2)
