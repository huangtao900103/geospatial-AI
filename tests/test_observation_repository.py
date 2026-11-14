"""Persistence/versioning/selection contracts in temporary databases, never live data."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from geo_services import GeoError
from server import Workspace
from catalog_adapters import normalize_item,search_url
from test_algorithm_catalog import observation

class ObservationTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.w=Workspace(Path(self.tmp.name)/'workspace.sqlite3');self.repo=self.w.observations
 def tearDown(self):self.w.close();self.tmp.cleanup()
 def seed(self,search_id='search_one',changed=False,source='pc_landsat_c2_l2'):
  template=observation(source);assets=copy.deepcopy(template['assets'])
  if changed:assets[1]['href']=assets[1]['href'].replace('red.TIF','new_red.TIF')
  item={**template,'id':'LC09_TEST' if source=='pc_landsat_c2_l2' else 'S2_TEST','cloud_cover':5,'bbox':[114,30,115,31],'assets':assets,'collection':template['collection']}
  self.w.save('data_search',{'id':search_id,'status':'completed','results':[{'source_id':source,'status':'succeeded','items':[item],'provenance':{'source_url':'https://example.invalid/search?'+search_id}}],'created_at':'2026-11-14'})
  return self.repo.ingest({'search_id':search_id})
 def test_immutable_version_and_dedup_across_search_queries(self):
  first=self.seed()['items'][0];again=self.seed('search_two');self.assertEqual(again['inserted'],0);self.assertEqual(again['updated'],1);self.assertEqual(first['id'],again['items'][0]['id'])
  self.assertEqual(set(again['items'][0]['search_ids']),{'search_one','search_two'})
  changed=self.seed('search_three',changed=True)['items'][0];self.assertNotEqual(first['id'],changed['id']);self.assertEqual(self.repo.get(first['id'])['metadata_sha256'],first['metadata_sha256'])
  self.assertEqual(self.repo.list()['stats']['observations'],2)
 def test_asset_index_and_bbox_cloud_date_filters(self):
  self.seed();result=self.repo.list({'source_id':'pc_landsat_c2_l2','cloud_max':'10','bbox':'114.2,30.2,114.3,30.3','start':'2025-11-01','end':'2025-11-30'})
  self.assertEqual(result['total'],1);self.assertEqual(result['stats']['assets'],8);self.assertEqual(self.repo.list({'bbox':'0,0,1,1'})['total'],0)
  self.assertEqual(self.repo.list({'cloud_max':'1'})['total'],0)
  for q in ({'bbox':'NaN,0,1,1'},{'start':'2025-02-30'},{'cloud_max':'inf'},{'limit':'0'},{'offset':'-1'},{'url':'https://invalid'}):
   with self.assertRaises(GeoError):self.repo.list(q)
 def test_ingest_uses_only_actual_successful_snapshot_ids(self):
  self.seed()
  for body in ({'search_id':'search_one','item_ids':['other']},{'search_id':'search_one','url':'https://other.invalid'}):
   with self.assertRaises(GeoError):self.repo.ingest(body)
  self.w.save('data_search',{'id':'failed','status':'failed','results':[{'source_id':'pc_landsat_c2_l2','status':'failed','items':[]}],'created_at':'2026-11-14'})
  with self.assertRaises(GeoError):self.repo.ingest({'search_id':'failed'})
 def test_ready_metadata_not_claimed_as_read_pixels(self):
  item=self.seed()['items'][0];self.assertTrue(item['stages']['analysis_ready']);self.assertFalse(item['stages']['pixel_verified']);self.assertEqual(len(item['compatible_algorithms']),11)
  self.assertTrue(item['analysis_context']['requires_user_aoi'])
 def body(self,obs):
  return {'requirement':'2025年11月NDVI','mode':'sentinel_cog','sampling':'native','bbox':[114.2,30.2,114.21,30.21],'periods':{'before':['2025-10-01','2025-10-31'],'after':['2025-11-01','2025-11-30']},'source_id':'pc_landsat_c2_l2','observation_id':obs['id']}
 def test_pinned_resource_validates_source_date_aoi_and_single_period(self):
  obs=self.seed()['items'][0];body=self.body(obs);prepared=self.w.production.resolve_input(body)
  self.assertEqual(prepared['scene_id'],obs['item_id']);self.assertEqual(prepared['observation_ref']['metadata_sha256'],obs['metadata_sha256'])
  for overrides in ({'source_id':'pc_sentinel2_l2a'},{'bbox':[113,30,113.01,30.01]},{'goal':'change'},{'periods':{'before':['2024-10-01','2024-10-31'],'after':['2024-11-01','2024-11-30']}}):
   with self.assertRaises(GeoError):self.w.production.resolve_input({**body,**overrides})
 def test_actual_binding_version_is_verified_before_pixel_read(self):
  obs=self.seed()['items'][0];body=self.w.production.resolve_input(self.body(obs));from production_services import make_plan
  plan=make_plan(body);assets={a['common_name']:{k:a.get(k) for k in ('href','scale','offset','nodata')} for a in obs['assets'] if a['common_name'] in ('red','nir','qa_pixel','qa_radsat')}
  scene={'id':obs['item_id'],'datetime':obs['datetime'],'platform':obs['platform'],'assets':assets};self.w.production.verify_bound_scene(plan,{'after':scene})
  for field in ('id','datetime','platform'):
   bad=copy.deepcopy(scene);bad[field]='different'
   with self.assertRaises(GeoError):self.w.production.verify_bound_scene(plan,{'after':bad})
  bad=copy.deepcopy(scene);bad['assets']['red']['offset']=0
  with self.assertRaises(GeoError) as error:self.w.production.verify_bound_scene(plan,{'after':bad})
  self.assertEqual(error.exception.code,'OBSERVATION_VERSION_CHANGED')
 def test_pinned_asset_identity_uses_band_key_not_rgb_preview_channel(self):
  obs=self.seed(source='pc_sentinel2_l2a')['items'][0]
  task_input={**self.body(obs),'source_id':'pc_sentinel2_l2a'};prepared=self.w.production.resolve_input(task_input)
  from production_services import make_plan
  plan=make_plan(prepared);actual={a['common_name']:{k:a.get(k) for k in ('href','scale','offset','nodata')} for a in obs['assets'] if a['common_name'] in ('red','nir','scl')}
  with patch.object(self.repo,'get',return_value={**obs,'assets':obs['assets']+[{'key':'visual','common_name':'red','href':'https://example.invalid/rgb.tif'}]}):
   self.w.production.verify_bound_scene(plan,{'after':{'id':obs['item_id'],'datetime':obs['datetime'],'assets':actual}})
 def test_pixel_evidence_requires_success_matching_hash_and_observation(self):
  obs=self.seed()['items'][0];base={'id':'task_a','status':'succeeded','created_at':'2026-11-14','result':{'provenance':{'input_sha256':'a'*64,'observation_ref':{'id':obs['id'],'metadata_sha256':obs['metadata_sha256']}}}}
  self.w.save('production_task',base)
  with self.assertRaises(GeoError):self.repo.record_pixel_use(obs['id'],'task_a','b'*64)
  other=self.seed('different',changed=True)['items'][0]
  with self.assertRaises(GeoError):self.repo.record_pixel_use(other['id'],'task_a','a'*64)
  self.repo.record_pixel_use(obs['id'],'task_a','a'*64);self.assertTrue(self.repo.get(obs['id'])['stages']['pixel_verified']);self.assertFalse(self.repo.get(other['id'])['stages']['pixel_verified'])
 def test_library_survives_reopen_without_rewriting_business_records(self):
  obs=self.seed()['items'][0];self.w.save('app',{'id':'business','name':'unchanged','created_at':'2026-11-14'});before=self.w.get('app','business');self.w.close();self.w=Workspace(Path(self.tmp.name)/'workspace.sqlite3');self.repo=self.w.observations
  self.assertEqual(self.repo.get(obs['id'])['metadata_sha256'],obs['metadata_sha256']);self.assertEqual(self.w.get('app','business'),before)
 def test_normalizer_strips_signed_queries_and_preserves_11_bands(self):
  feature={'id':'s2','collection':'sentinel-2-l2a','bbox':[114,30,115,31],'properties':{'datetime':'2025-11-14T02:00:00Z','platform':'sentinel-2b'},'assets':{'B03':{'href':'https://example.invalid/green.tif?sig=credential&se=time','type':'image/tiff','bands':[{'name':'B03','eo:common_name':'green','scale':.0001,'offset':-.1}]}}}
  item=normalize_item('cdse_sentinel2_l2a',feature,[114.2,30.2,114.3,30.3],{'start':'2025-11-01','end':'2025-11-30'})
  self.assertNotIn('credential',json.dumps(item));self.assertEqual(item['assets'][0]['scale'],.0001);self.assertEqual(item['assets'][0]['offset'],-.1);self.assertTrue(item['assets'][0]['auth_required'])
  self.assertNotIn('datetime=',search_url('earthsearch_cop_dem_glo30',[114,30,115,31],{'start':'2025-01-01','end':'2025-12-31'},1))
 def test_landsat_selection_plan_and_success_lineage_keep_actual_source(self):
  plan=self.w.data_catalog.create_plan({'question':'2025年11月Landsat NDVI','goal':'ndvi','source_ids':['pc_landsat_c2_l2'],'bbox':[114.2,30.2,114.21,30.21],'period':{'start':'2025-11-01','end':'2025-11-30'}})
  self.assertTrue(plan['executable']);self.assertEqual(plan['execution_contract']['body']['source_id'],'pc_landsat_c2_l2')
  provenance={'source_id':'pc_landsat_c2_l2','source_type':'public_satellite_pixels','input_sha256':'a'*64,'bbox':[114.2,30.2,114.21,30.21],'source_details':{'scenes':{'after':{'datetime':'2025-11-29T02:00:00Z'}}}}
  self.w.save('production_task',{'id':'land_actual','status':'succeeded','created_at':'2026-11-14','result':{'provenance':provenance}})
  evidence,used,related=self.w.data_catalog.execution_evidence({'task_type':'production','task_id':'land_actual'},'ndvi','指定坐标小窗',None,None,'本次分析来源')
  self.assertIsNone(related);self.assertEqual(used[0]['source_id'],'pc_landsat_c2_l2')
 def test_discovery_plan_includes_new_provider_constraints(self):
  sources=['pc_cop_dem_glo30','cdse_sentinel2_l2a'];plan=self.w.data_catalog.create_plan({'goal':'general_discovery','source_ids':sources,'bbox':[114.2,30.2,114.21,30.21],'period':{'start':'2025-11-01','end':'2025-11-30'}})
  self.assertTrue(plan['executable']);self.assertEqual(set(plan['execution_contract']['body']['source_ids']),set(sources));self.assertTrue(plan['not_executed'])
 def test_cache_key_separates_sensor_and_observation_versions(self):
  obs=self.seed()['items'][0];a=self.w.production.resolve_input(self.body(obs));b={k:v for k,v in a.items() if k not in ('observation_ref','observation_id','scene_id')};c={**b,'source_id':'pc_sentinel2_l2a'}
  keys=[self.w.executions.input_key('sentinel_native',v) for v in (a,b,c)]
  self.assertEqual(len(set(keys)),3)

if __name__=='__main__':unittest.main()
