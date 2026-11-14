"""Business-to-native-derivative lifecycle regressions using labelled local fixtures."""
import copy
import time
import unittest
from geo_services import GeoError
import test_agriculture_services as fixtures

class AgricultureWorkflowsTest(unittest.TestCase):
 setUp=fixtures.AgricultureServicesTest.setUp
 tearDown=fixtures.AgricultureServicesTest.tearDown
 seed_production=fixtures.AgricultureServicesTest.seed_production
 source_bbox=fixtures.AgricultureServicesTest.source_bbox
 polygon=fixtures.AgricultureServicesTest.polygon
 dataset=fixtures.AgricultureServicesTest.dataset
 def wait_question(self,id):
  for _ in range(800):
   q=self.w.assistant.get(id)
   if q['status']!='processing':return q
   time.sleep(.01)
  self.fail('question did not settle')
 def body(self,id='vegetation_cover',parameters=None):
  return {'application_id':id,'context':{},'parameters':parameters or {'source_ref':'production:test_ndvi','soil_ndvi':.2,'vegetation_ndvi':.8}}
 def test_fvc_form_is_explicit_and_routes_real_native_derivative(self):
  payload,ref=self.w.applications.prepare(self.body());plan=self.w.executions.prepare(payload['question'],payload['context'])
  self.assertTrue(plan['executable'],plan);self.assertEqual(plan['tool']['id'],'agriculture_analysis');self.assertEqual(plan['tool']['inputs']['source_task_id'],'test_ndvi')
  self.assertEqual(ref['method']['source_ref']['artifact_sha256'],plan['source_snapshot']['source']['artifact_sha256'])
  for change in ({'soil_ndvi':None},{'vegetation_ndvi':None},{'source_ref':''},{'soil_ndvi':.9}):
   with self.subTest(change=change),self.assertRaises(GeoError):self.w.applications.prepare(self.body(parameters={**self.body()['parameters'],**change}))
 def test_fvc_missing_source_and_endmembers_are_clarified_not_invented(self):
  plan=self.w.executions.prepare('估算NDVI植被覆盖度',{})
  self.assertFalse(plan['executable']);self.assertEqual(set(plan['missing_fields']),{'source_type','source_task_id','soil_ndvi','vegetation_ndvi'})
  for question in ('估算FVC和LAI','根据FVC给出产量','全市FVC植被覆盖度'):
   plan=self.w.executions.prepare(question,{})
   self.assertFalse(plan['executable']);self.assertEqual(plan['status'],'unsupported')
 def test_fvc_answer_map_download_source_and_cache_are_same_task(self):
  a=self.wait_question(self.w.applications.run(self.body())['id']);self.assertEqual(a['status'],'answered',a)
  self.assertEqual(a['binding']['type'],'agriculture');self.assertEqual(a['answer']['result_ref']['type'],'agriculture');self.assertEqual(a['answer']['metrics'][1]['value'],8)
  self.assertTrue(any(d['url'].endswith('bundle.zip') for d in a['answer']['downloads']))
  b=self.wait_question(self.w.applications.run(self.body())['id']);self.assertEqual(a['binding']['task_id'],b['binding']['task_id']);self.assertTrue(b['binding']['reused'])
  c=self.wait_question(self.w.assistant.create({'question':'这些结果来自哪里？','context':{'reference_question_id':a['id']}})['id']);self.assertEqual(c['status'],'answered',c);self.assertEqual(c['binding']['task_id'],a['binding']['task_id']);self.assertEqual(c['answer']['map_effect'],'none')
  altered=self.body();altered['parameters']['soil_ndvi']=.1
  d=self.wait_question(self.w.applications.run(altered)['id']);self.assertEqual(d['status'],'answered',d);self.assertNotEqual(d['binding']['task_id'],a['binding']['task_id'])
 def test_zonal_form_freezes_user_geometry_and_uses_zonal_executor(self):
  data=self.dataset([self.polygon(500000,3399970,500030,3400000)])
  body=self.body('field_zonal_statistics',{'source_ref':'production:test_ndvi','field':'auto','threshold':.5});body['context']={'dataset_id':data['id']}
  payload,ref=self.w.applications.prepare(body);self.assertEqual(ref['method']['dataset']['sha256'],data['sha256'])
  q=self.wait_question(self.w.applications.run(body)['id']);self.assertEqual(q['status'],'answered',q);r=self.w.agriculture.get(q['binding']['task_id'])['result']
  self.assertEqual(r['summary']['zone_count'],1);self.assertEqual(r['summary']['unique_valid_pixel_count'],8);self.assertEqual(r['geojson']['features'][0]['properties']['external_id'],'ext-1')
 def test_new_spectral_business_forms_respect_sensor_and_threshold_domain(self):
  context={'bbox':[114.2,30.5,114.21,30.51],'period':{'start':'2025-11-01','end':'2025-11-30'}}
  payload,_=self.w.applications.prepare({'application_id':'crop_spectral','context':context,'parameters':{'index_id':'savi','source_id':'auto','threshold':1.2,'operator':'gte'}})
  plan=self.w.executions.prepare(payload['question'],payload['context']);self.assertTrue(plan['executable'],plan);self.assertEqual(plan['source_snapshot']['threshold'],1.2)
  with self.assertRaises(GeoError):self.w.applications.prepare({'application_id':'vegetation_status','context':context,'parameters':{'threshold':1.2}})
  with self.assertRaises(GeoError):self.w.applications.prepare({'application_id':'crop_rededge','context':context,'parameters':{'source_id':'pc_landsat_c2_l2'}})
 def test_derived_text_conditions_must_match_source_dates_index_and_implemented_comparison(self):
  data=self.dataset([self.polygon(500000,3399970,500030,3400000)])
  context={'source_type':'production','source_task_id':'test_ndvi','dataset_id':data['id']}
  for question,extra in [('按地块统计水稻面积',{}),('按地块统计NDRE均值',{}),('按地块统计2025年10月NDVI均值',{}),('按地块统计2024年NDVI均值',{}),('按地块统计NDVI大于0.5的面积',{}),('按地块统计NDVI不低于0.5的面积',{'threshold':.3}),('按地块统计农田面积',{}),('按地块统计NDVI不低于0.3且不高于0.5的面积',{}),('按地块统计NDVI不低于0.3%的面积',{}),('按地块统计2025-11-01至2025-11-99 NDVI均值',{})]:
   with self.subTest(question=question):self.assertFalse(self.w.executions.prepare(question,{**context,**extra})['executable'])
  plan=self.w.executions.prepare('按地块统计2025年11月NDVI不低于0.5的面积',context)
  self.assertTrue(plan['executable'],plan);self.assertEqual(plan['tool']['inputs']['threshold'],.5)
 def test_fvc_and_zonal_named_followups_read_original_execution(self):
  q=self.wait_question(self.w.applications.run(self.body())['id']);self.assertEqual(q['status'],'answered',q)
  for text in ('下载FVC结果','这个植被覆盖度结果的来源是什么','下载分区统计结果','分区统计结果的精度如何'):
   with self.subTest(text=text):
    plan=self.w.executions.prepare(text,{'reference_execution_id':q['execution_ref']['id']});self.assertTrue(plan['executable'],plan);self.assertEqual(plan['tool']['id'],'result_reference')
  plan=self.w.executions.prepare('下载FVC并重新计算',{'reference_execution_id':q['execution_ref']['id']});self.assertFalse(plan['executable'])
 def test_derived_cannot_ignore_other_form_parameters_or_map_extent(self):
  context={'source_type':'production','source_task_id':'test_ndvi','soil_ndvi':.2,'vegetation_ndvi':.8}
  for extra in ({'dataset_id':'other'},{'threshold':.4},{'field':'after_index'},{'bbox':[113,30,113.01,30.01]},{'period':{'start':'2025-01-01','end':'2025-01-31'}}):
   with self.subTest(extra=extra):self.assertFalse(self.w.executions.prepare('估算FVC植被覆盖度',{**context,**extra})['executable'])
