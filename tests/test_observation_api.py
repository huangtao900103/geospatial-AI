"""Actual HTTP contracts for the isolated EO library and future model clients."""
import json,os,tempfile,threading,unittest
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from backend.server import Workspace,LocalServer,make_handler
from test_algorithm_catalog import observation
class ObservationAPITest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.w=Workspace(Path(self.tmp.name)/'workspace.sqlite3');self.server=LocalServer(('127.0.0.1',0),make_handler(self.w));self.server.quiet=True;self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.server.server_port)
 def tearDown(self):self.w.close();self.server.shutdown();self.server.server_close();self.thread.join(2);self.tmp.cleanup()
 def req(self,path,body=None):
  try:r=urlopen(Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'}),timeout=5)
  except HTTPError as e:r=e
  return r.status,json.load(r)
 def seed(self):
  item=observation('pc_landsat_c2_l2');item.update(id='LC09_API',bbox=[114,30,115,31],cloud_cover=5)
  self.w.save('data_search',{'id':'api_search','results':[{'source_id':item['source_id'],'status':'succeeded','items':[item],'provenance':{}}],'created_at':'2026-11-14'})
  code,r=self.req('/api/observations/ingest',{'search_id':'api_search'});self.assertEqual(code,201);return r['items'][0]
 def test_catalogue_and_ai_operations_expose_typed_inputs_without_arbitrary_code(self):
  code,catalog=self.req('/api/algorithm-contracts');self.assertEqual(code,200);self.assertEqual(len(catalog['contracts']),23)
  code,tools=self.req('/api/ai-tools');self.assertEqual(code,200);self.assertFalse(tools['model_service_configured'])
  operation_ids=[operation['id'] for operation in tools['operations']]
  self.assertEqual(len(operation_ids),len(set(operation_ids)))
  self.assertTrue({'run_spectral_partition_workflow','get_analysis_workflow','analyze_series_trend'}.issubset(operation_ids))
  self.assertTrue({'list_agriculture_research','get_agriculture_series','create_agriculture_change_review'}.issubset({item['id'] for item in tools['operations']}))
  discovery=next(v for v in tools['operations'] if v['id']=='discover_imagery')['input_schema'];self.assertEqual(len(discovery['properties']['source_ids']['items']['enum']),7);self.assertFalse(discovery['additionalProperties'])
  regional=next(v for v in tools['operations'] if v['id']=='list_regional_agriculture_products');self.assertEqual(regional['path'],'/api/agriculture-atlas');self.assertEqual(regional['method'],'GET')
 def test_real_snapshot_ingest_detail_filter_and_match_endpoints(self):
  obs=self.seed();code,detail=self.req('/api/observations/'+obs['id']);self.assertEqual(code,200);self.assertEqual(detail['metadata_sha256'],obs['metadata_sha256'])
  code,listed=self.req('/api/observations?bbox=114.2,30.2,114.3,30.3&cloud_max=10');self.assertEqual(code,200);self.assertEqual(listed['total'],1)
  code,matched=self.req('/api/algorithm-matches',{'observation_id':obs['id'],'index_id':'ndmi'});self.assertEqual(code,200);self.assertEqual(matched['ready_count'],1);self.assertFalse(matched['pixels_read'])
  self.assertEqual(self.req('/api/algorithm-matches',{'observation_id':obs['id'],'url':'https://other.invalid'})[0],400)
 def test_application_freezes_source_and_observation_before_execution(self):
  obs=self.seed()
  with patch.object(self.w.assistant,'launch'):
   code,q=self.req('/api/application-runs',{'application_id':'spectral_lab','context':{'observation_id':obs['id'],'bbox':[114.2,30.2,114.21,30.21],'period':{'start':'2025-11-14','end':'2025-11-14'}},'parameters':{'source_id':'pc_landsat_c2_l2','index_id':'ndvi'},'request_id':'api-pinned'})
  self.assertEqual(code,201);self.assertEqual(q['context']['observation_id'],obs['id']);self.assertEqual(q['context']['source_id'],'pc_landsat_c2_l2');self.assertEqual(q['application_ref']['method']['reference_resolution_m'],30)
  self.assertEqual(q['application_ref']['method']['observation_ref']['metadata_sha256'],obs['metadata_sha256'])
if __name__=='__main__':unittest.main()
