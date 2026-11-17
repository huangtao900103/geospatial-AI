"""Isolated fixtures exercise real scene operations, never production seed data."""
import csv,io,json,hashlib,math,tempfile,threading,unittest,zipfile
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from backend.server import Workspace,LocalServer,make_handler
from backend.workflows.scene_workflow_services import NAMES

def polygon(ident,status='在建',value=3):return {'type':'Feature','id':ident,'properties':{'项目编号':ident,'状态':status,'容量':value},'geometry':{'type':'Polygon','coordinates':[[[113.4,30.3],[113.401,30.3],[113.401,30.301],[113.4,30.301],[113.4,30.3]]]}}
def line(ident,device):return {'type':'Feature','id':ident,'properties':{'设备编号':device},'geometry':{'type':'LineString','coordinates':[[0,0],[.001,0]]}}
def fc(features):return {'type':'FeatureCollection','features':features}
class SceneWorkflowsTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.ws=Workspace(Path(self.temp.name)/'workspace.sqlite3');self.http=LocalServer(('127.0.0.1',0),make_handler(self.ws));self.http.quiet=True;self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.http.server_port)
 def tearDown(self):self.ws.close();self.http.shutdown();self.http.server_close();self.thread.join();self.temp.cleanup()
 def request(self,path,body=None,raw=False):
  req=Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
  try:r=urlopen(req,timeout=10)
  except HTTPError as e:r=e
  data=r.read();return r.status,data if raw else json.loads(data)
 def dataset(self,features):return self.ws.create_dataset({'name':'隔离自动检查输入，不进入正式库','geojson':fc(features)})
 def test_all_22_scenes_run_and_persist_exact_dataset_version(self):
  area=self.dataset([polygon('A')]);track=self.dataset([line('T','D')])
  for sid in NAMES:
   d=track if sid=='machinery' else area
   code,r=self.request('/api/scene-workflows/'+sid+'/run',{'dataset_id':d['id']});self.assertEqual(code,201,(sid,r));self.assertEqual(r['provenance']['sha256'],d['sha256']);self.assertEqual(r['summary']['feature_count'],1)
  code,c=self.request('/api/scene-workflows');self.assertEqual(code,200);self.assertEqual(len(c['bindings']),22);self.assertEqual(len(c['runs']),22)
  self.assertEqual(self.ws.get_dataset(area['id'])['sha256'],area['sha256'])
 def test_project_filter_groups_area_unique_ids_and_non_numeric(self):
  d=self.dataset([polygon('A','在建',0),polygon('B','完工',None),polygon('C','在建','12'),polygon('D','在建',True)])
  _,r=self.request('/api/scene-workflows/high_standard/run',{'dataset_id':d['id'],'group_by':'状态','entity_field':'项目编号','measure_field':'容量','measure_unit':'吨','query':'在建'})
  s=r['summary'];self.assertEqual(s['feature_count'],3);self.assertEqual(s['filtered_out_count'],1);self.assertEqual(s['groups'],{'在建':3});self.assertEqual(s['entity_count'],3);self.assertEqual(s['measure_sum'],0);self.assertEqual(s['measure_valid_count'],1);self.assertEqual(s['measure_invalid_count'],2);self.assertGreater(s['area_sum_ha'],0)
 def test_all_missing_numeric_is_unknown_not_zero(self):
  d=self.dataset([polygon('A',value=None)])
  _,r=self.request('/api/scene-workflows/coldchain/run',{'dataset_id':d['id'],'measure_field':'容量','measure_unit':'吨'});self.assertIsNone(r['summary']['measure_sum']);self.assertEqual(r['summary']['measure_missing_count'],1)
 def test_tracks_length_independently_calculated_and_no_swath_area(self):
  d=self.dataset([line('T1','M1'),line('T2','M1'),line('T3','M2')]);_,r=self.request('/api/scene-workflows/machinery/run',{'dataset_id':d['id'],'entity_field':'设备编号'});s=r['summary']
  self.assertEqual(s['entity_count'],2);self.assertEqual(s['line_count'],3);self.assertAlmostEqual(s['line_length_m'],6371008.8*math.radians(.001)*3,places=3);self.assertEqual(s['polygon_feature_count'],0);self.assertNotIn('作业面积',r['geojson']['features'][0]['properties'])
 def test_missing_geometry_fields_units_and_empty_filter_do_not_succeed(self):
  d=self.dataset([polygon('A')]);track=self.dataset([line('T','D')])
  for sid,body in [('machinery',{'dataset_id':d['id']}),('high_standard',{'dataset_id':track['id']}),('high_standard',{'dataset_id':d['id'],'group_by':'不存在'}),('coldchain',{'dataset_id':d['id'],'measure_field':'容量'}),('high_standard',{'dataset_id':d['id'],'query':'无匹配内容'})]:
   code,_=self.request('/api/scene-workflows/'+sid+'/run',body);self.assertEqual(code,422)
  self.assertEqual(self.ws.list('analysis'),[]);self.assertEqual(self.ws.list('scene_binding'),[])
 def test_archives_have_csv_geojson_provenance_and_valid_manifest(self):
  f=polygon('A');f['properties']['文本']='=1+1';d=self.dataset([f]);_,r=self.request('/api/scene-workflows/high_standard/run',{'dataset_id':d['id']});code,data=self.request('/api/results/analysis/'+r['id']+'/bundle.zip',raw=True);self.assertEqual(code,200)
  with zipfile.ZipFile(io.BytesIO(data)) as z:
   self.assertIsNone(z.testzip());self.assertTrue({'summary.csv','features.csv','result.geojson','provenance.json'}<=set(z.namelist()))
   for entry in json.loads(z.read('manifest.json'))['entries']:self.assertEqual(hashlib.sha256(z.read(entry['path'])).hexdigest(),entry['sha256'])
   self.assertIn("'=1+1",z.read('features.csv').decode('utf-8-sig'))
 def test_unknown_scene_and_synthetic_data_rejected(self):
  d=self.dataset([polygon('A')]);code,_=self.request('/api/scene-workflows/not-real/run',{'dataset_id':d['id']});self.assertEqual(code,404)
  d['source_type']='synthetic';self.ws.save('dataset',d);code,_=self.request('/api/scene-workflows/high_standard/run',{'dataset_id':d['id']});self.assertEqual(code,422)
 def test_bindings_survive_workspace_reopen(self):
  d=self.dataset([polygon('A')]);_,r=self.request('/api/scene-workflows/high_standard/run',{'dataset_id':d['id'],'group_by':'状态'})
  reopened=Workspace(self.ws.db_path)
  try:self.assertEqual(reopened.scene_workflows.catalog()['bindings'][0]['latest_analysis_id'],r['id'])
  finally:reopened.close()
 def test_dataset_id_strict_type_and_length_are_422_for_run_and_scope(self):
  for value in (None,False,True,0,1,1.0,[],{},'', '  ', 'x'*101):
   for action in ('run','scope'):
    with self.subTest(value=value,action=action):
     code,r=self.request('/api/scene-workflows/high_standard/'+action,{'dataset_id':value});self.assertEqual(code,422,r);self.assertEqual(r['error']['code'],'INVALID_SCENE_DATASET')
  self.assertEqual(self.ws.list('analysis'),[]);self.assertEqual(self.ws.list('scene_binding'),[])
 def test_area_calculations_preserve_original_fields_and_match_original_measures(self):
  a=polygon('A');b=polygon('B')
  a['properties'].update(computed_area_ha=123,scene_geometry_area_ha=456)
  b['properties'].update(computed_area_ha=789,scene_geometry_area_ha_2=999)
  d=self.dataset([a,b]);code,r=self.request('/api/scene-workflows/high_standard/run',{'dataset_id':d['id'],'measure_field':'computed_area_ha','measure_unit':'台账公顷','group_by':'computed_area_ha'})
  self.assertEqual(code,201,r);s=r['summary'];self.assertEqual(s['measure_sum'],912);self.assertEqual(s['groups'],{'123':1,'789':1});self.assertEqual(s['computed_fields']['area_ha'],'scene_geometry_area_ha_3')
  for source,result in zip((a,b),r['geojson']['features']):
   for key,value in source['properties'].items():self.assertEqual(result['properties'][key],value)
   self.assertGreater(result['properties'][s['computed_fields']['area_ha']],0)
  self.assertEqual(self.ws.get_dataset(d['id'])['sha256'],d['sha256'])
 def test_track_calculations_preserve_original_length_and_avoid_all_input_fields(self):
  a=line('A','M1');b=line('B','M2')
  a['properties'].update(scene_length_m=100,scene_geometry_length_m=500)
  b['properties'].update(scene_length_m=200,scene_geometry_length_m_2=600)
  d=self.dataset([a,b]);code,r=self.request('/api/scene-workflows/machinery/run',{'dataset_id':d['id'],'measure_field':'scene_length_m','measure_unit':'台账米','group_by':'scene_length_m'})
  self.assertEqual(code,201,r);s=r['summary'];self.assertEqual(s['measure_sum'],300);self.assertEqual(s['groups'],{'100':1,'200':1});self.assertEqual(s['computed_fields']['length_m'],'scene_geometry_length_m_3')
  for source,result in zip((a,b),r['geojson']['features']):
   for key,value in source['properties'].items():self.assertEqual(result['properties'][key],value)
   self.assertAlmostEqual(result['properties'][s['computed_fields']['length_m']],6371008.8*math.radians(.001),places=3)
 def test_scope_uses_run_filter_and_excludes_remote_completed_geometry(self):
  a=polygon('A','在建');b=polygon('B','已完工')
  for point in b['geometry']['coordinates'][0]:point[0]+=2
  d=self.dataset([a,b]);body={'dataset_id':d['id'],'query':'在建'}
  code,scope=self.request('/api/scene-workflows/high_standard/scope',body);self.assertEqual(code,200,scope)
  code,result=self.request('/api/scene-workflows/high_standard/run',body);self.assertEqual(code,201,result)
  self.assertEqual(scope['selected_count'],result['summary']['feature_count']);self.assertEqual(scope['input_count'],2);self.assertEqual(scope['bbox'],[113.4,30.3,113.401,30.301]);self.assertEqual(scope['dataset_id'],d['id']);self.assertEqual(scope['version'],d['version']);self.assertEqual(scope['sha256'],d['sha256']);self.assertEqual(scope['query'],'在建')
  code,all_scope=self.request('/api/scene-workflows/high_standard/scope',{'dataset_id':d['id'],'query':''});self.assertEqual(code,200);self.assertEqual(all_scope['selected_count'],2);self.assertGreater(all_scope['bbox'][2],115)
  for action in ('scope','run'):
   code,r=self.request('/api/scene-workflows/high_standard/'+action,{'dataset_id':d['id'],'query':'不存在的筛选词'});self.assertEqual(code,422,r);self.assertEqual(r['error']['code'],'SCENE_EMPTY_FILTER')
 def test_scope_keeps_point_bbox_degenerate_and_rejects_extra_fields(self):
  point={'type':'Feature','id':'P','properties':{'名称':'定位点'},'geometry':{'type':'Point','coordinates':[113.4,30.3]}}
  d=self.dataset([point]);code,scope=self.request('/api/scene-workflows/breeding/scope',{'dataset_id':d['id']});self.assertEqual(code,200,scope);self.assertEqual(scope['bbox'],[113.4,30.3,113.4,30.3])
  code,_=self.request('/api/scene-workflows/breeding/scope',{'dataset_id':d['id'],'group_by':'名称'});self.assertEqual(code,400)

 def test_frozen_feature_groups_match_backend_bool_null_and_float_labels(self):
  features=[polygon('A',True),polygon('B',None),polygon('C',1.0)]
  d=self.dataset(features);code,r=self.request('/api/scene-workflows/high_standard/run',{'dataset_id':d['id'],'group_by':'状态'})
  self.assertEqual(code,201,r);s=r['summary'];self.assertEqual(s['feature_groups'],{'A':'True','B':'(未分组)','C':'1.0'});self.assertEqual(s['groups'],{'True':1,'(未分组)':1,'1.0':1})
  self.assertIs(r['geojson']['features'][0]['properties']['状态'],True);self.assertIsNone(r['geojson']['features'][1]['properties']['状态']);self.assertEqual(r['geojson']['features'][2]['properties']['状态'],1.0)
  code,ungrouped=self.request('/api/scene-workflows/high_standard/run',{'dataset_id':d['id']});self.assertEqual(code,201);self.assertEqual(ungrouped['summary']['feature_groups'],{})
