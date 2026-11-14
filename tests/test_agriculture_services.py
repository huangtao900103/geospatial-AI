"""Native agricultural derivative tests with explicitly synthetic local rasters."""
import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_geom,transform_bounds
from agriculture_services import AgricultureServices,capabilities,VERSION
from geo_services import GeoError
from server import Workspace


def wait(service,id):
 for _ in range(500):
  task=service.get(id)
  if task['status'] not in ('queued','running'):return task
  time.sleep(.01)
 raise AssertionError('agriculture task timed out')


class AgricultureServicesTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.w=Workspace(self.root/'workspace.sqlite3')
  self.service=AgricultureServices(self.w);self.transform=from_origin(500000,3400000,10,10)
  self.values=np.array([[.1,.2,.9],[.4,np.nan,.8],[0.,.6,1.]],dtype='float32')
  self.seed_production('test_ndvi')
 def tearDown(self):self.service.close();self.w.close();self.temp.cleanup()
 def polygon(self,left,bottom,right,top,hole=None):
  rings=[[[left,bottom],[right,bottom],[right,top],[left,top],[left,bottom]]]
  if hole:
   a,b,c,d=hole;rings.append([[a,b],[c,b],[c,d],[a,d],[a,b]])
  return json.loads(json.dumps(transform_geom('EPSG:32649','EPSG:4326',{'type':'Polygon','coordinates':rings})))
 def source_bbox(self):return list(transform_bounds('EPSG:32649','EPSG:4326',500000,3399970,500030,3400000))
 def seed_production(self,id,index='ndvi',goal='ndvi',values=None):
  data=self.values if values is None else values;folder=(Path(self.w.db_path).parent/'production')/id;folder.mkdir(parents=True)
  valid=np.isfinite(data).astype('float32');inside=np.ones(data.shape,dtype='float32')
  arrays=[data,valid,inside];names=['after_'+index,'common_valid_mask','aoi_mask']
  if goal=='change':arrays.append(data*.1);names.append('delta_'+index)
  with rasterio.open(folder/'analysis.tif','w',driver='GTiff',width=3,height=3,count=len(arrays),dtype='float32',crs='EPSG:32649',transform=self.transform,nodata=float('nan')) as ds:
   for i,(name,array) in enumerate(zip(names,arrays),1):ds.write(array,i);ds.set_band_description(i,name)
  task={'id':id,'status':'succeeded','created_at':'2026-11-14','plan':{'mode':'sentinel_cog','sampling':'native','goal':goal,'index_id':index},'result':{'summary':{'index_id':index,'index':{'id':index,'label':index.upper(),'threshold_range':[-2,2] if index in ('savi','evi2') else [-1,1]}},'provenance':{'source_type':'public_satellite_pixels','index_id':index,'bbox':self.source_bbox(),'input_sha256':'a'*64,'crs':'EPSG:32649','source_details':{'scenes':{'after':{'datetime':'2025-11-14T02:30:00Z'}}}}}}
  self.w.save('production_task',task);return task
 def seed_io(self):
  folder=self.w.cropland.output_dir/'test_io';folder.mkdir(parents=True)
  data=np.array([[5,5,10],[1,0,5],[2,5,5]],dtype='uint8')
  with rasterio.open(folder/'classification.tif','w',driver='GTiff',width=3,height=3,count=2,dtype='uint8',crs='EPSG:32649',transform=self.transform,nodata=0) as ds:
   ds.write(data,1);ds.write(np.ones((3,3),dtype='uint8'),2);ds.set_band_description(1,'classification');ds.set_band_description(2,'aoi_mask')
  self.w.save('cropland_task',{'id':'test_io','status':'succeeded','created_at':'2026-11-14','plan':{'bbox':self.source_bbox()},'result':{'summary':{'reference_year':2024},'provenance':{'source_type':'public_classification_product','operation':'published_classification_extraction','product':{'id':'io_lulc_2024'},'year':2024,'selected_bbox':self.source_bbox(),'classified_window_sha256':'b'*64}}})
 def dataset(self,geometries):
  return self.w.create_dataset({'name':'Explicit test parcels','geojson':{'type':'FeatureCollection','features':[{'type':'Feature','id':'parcel-'+str(i+1),'properties':{'name':'Test parcel '+str(i+1),'external_id':'ext-'+str(i+1)},'geometry':g} for i,g in enumerate(geometries)]}})
 def fvc_request(self):return {'algorithm_id':'fvc','source_type':'production','source_task_id':'test_ndvi','soil_ndvi':.2,'vegetation_ndvi':.8}
 def test_fvc_explicit_endmembers_native_values_unclamped_and_clamp_counts(self):
  t=wait(self.service,self.service.create(self.fvc_request())['id']);self.assertEqual(t['status'],'succeeded',t.get('error'));s=t['result']['summary']
  raw=(self.values.astype('float64')-.2)/.6;expected=np.clip(raw,0,1);self.assertAlmostEqual(s['mean_fvc'],float(np.nanmean(expected)),places=7)
  self.assertEqual(s['clamped_low_count'],int((raw<0).sum()));self.assertEqual(s['clamped_high_count'],int((raw>1).sum()));self.assertEqual(s['valid_pixel_count'],8);self.assertAlmostEqual(s['valid_ratio'],8/9)
  with rasterio.open(self.service.artifact(t['id'],'analysis.tif')) as ds:
   self.assertEqual(ds.descriptions,('fvc','fvc_unclamped','common_valid_mask','aoi_mask','after_ndvi'));np.testing.assert_allclose(ds.read(1),expected,atol=1e-7,equal_nan=True);np.testing.assert_allclose(ds.read(2),raw,atol=1e-7,equal_nan=True)
  self.assertAlmostEqual(s['estimated_vegetation_area_ha'],float(np.nansum(expected))*.01,places=8)
  self.assertEqual(t['plan']['source']['actual_dates']['after'],'2025-11-14T02:30:00Z');self.assertEqual(s['endmember_source'],'explicit_user_values_not_locally_calibrated')
 def test_fvc_rejects_missing_or_inverted_endmembers_other_index_and_two_period_mask(self):
  for change in ({'soil_ndvi':None},{'soil_ndvi':True},{'vegetation_ndvi':.2},{'vegetation_ndvi':1.1},{'field':'after_index'},{'dataset_id':'x'},{'threshold':.2}):
   with self.subTest(change=change),self.assertRaises(GeoError):self.service.prepare({**self.fvc_request(),**change})
  self.seed_production('two_period',goal='change');self.seed_production('savi_source',index='savi')
  for id in ('two_period','savi_source'):
   with self.assertRaises(GeoError) as error:self.service.prepare({**self.fvc_request(),'source_task_id':id})
   self.assertEqual(error.exception.code,'FVC_REQUIRES_SINGLE_NDVI')
 def test_zonal_full_partial_outside_holes_and_overlap_union_are_distinct(self):
  full=self.polygon(500000,3399970,500030,3400000);hole=self.polygon(500000,3399970,500030,3400000,(500010,3399980,500020,3399990))
  partial=self.polygon(499990,3399980,500010,3400010);outside=self.polygon(501000,3401000,501020,3401020)
  data=self.dataset([full,copy.deepcopy(full),hole,partial,outside]);request={'algorithm_id':'zonal_statistics','source_type':'production','source_task_id':'test_ndvi','dataset_id':data['id'],'threshold':.5}
  t=wait(self.service,self.service.create(request)['id']);self.assertEqual(t['status'],'succeeded',t.get('error'));features=t['result']['geojson']['features'];rows=[f['properties'] for f in features]
  self.assertEqual([r['agri_coverage_status'] for r in rows],['full','full','full','partial','outside'])
  self.assertEqual(rows[0]['external_id'],'ext-1');self.assertEqual(rows[0]['agri_zone_name'],'Test parcel 1');self.assertAlmostEqual(rows[0]['agri_mean'],float(np.nanmean(self.values)),places=7)
  self.assertEqual(rows[2]['agri_covered_pixel_count'],8);self.assertEqual(rows[3]['agri_covered_pixel_count'],2);self.assertIsNone(rows[3]['agri_source_coverage_ratio'])
  self.assertEqual(rows[4]['agri_valid_pixel_count'],0);self.assertIsNone(rows[4]['agri_mean']);self.assertIsNone(rows[4]['agri_valid_ratio_covered'])
  s=t['result']['summary'];self.assertEqual(s['unique_covered_pixel_count'],9);self.assertEqual(s['unique_valid_pixel_count'],8);self.assertGreater(s['sum_zone_covered_area_ha'],s['unique_covered_area_ha']);self.assertAlmostEqual(s['unique_selected_area_ha'],.04)
  self.assertIn('不代表全地块覆盖',s['source_coverage_note'])
 def test_classification_cloud_and_nodata_unknown_not_non_cropland(self):
  self.seed_io();data=self.dataset([self.polygon(500000,3399970,500030,3400000)])
  t=wait(self.service,self.service.create({'algorithm_id':'zonal_statistics','source_type':'cropland','source_task_id':'test_io','dataset_id':data['id']})['id']);self.assertEqual(t['status'],'succeeded',t.get('error'))
  props=t['result']['geojson']['features'][0]['properties'];self.assertEqual(props['agri_valid_pixel_count'],7);self.assertEqual(props['agri_unknown_pixel_count'],2);self.assertEqual(props['agri_cropland_pixel_count'],5);self.assertAlmostEqual(props['agri_cropland_area_ha'],.05);self.assertAlmostEqual(props['agri_valid_ratio_covered'],7/9)
  self.assertAlmostEqual(t['result']['summary']['unique_unknown_area_ha'],.02)
 def test_threshold_range_follows_actual_source_index_and_delta_is_explicit(self):
  self.seed_production('savi_source',index='savi');data=self.dataset([self.polygon(500000,3399970,500030,3400000)])
  body={'algorithm_id':'zonal_statistics','source_type':'production','source_task_id':'savi_source','dataset_id':data['id'],'threshold':1.5};self.assertEqual(self.service.prepare(body)['threshold'],1.5)
  with self.assertRaises(GeoError):self.service.prepare({**body,'source_task_id':'test_ndvi'})
  self.seed_production('change_source',goal='change');p=self.service.prepare({**body,'source_task_id':'change_source','field':'delta_index','threshold':.1});self.assertEqual(p['value_band'],'delta_ndvi')
  self.seed_io()
  with self.assertRaises(GeoError):self.service.prepare({**body,'source_type':'cropland','source_task_id':'test_io','field':'classification'})
 def test_frozen_source_and_dataset_changes_fail_instead_of_rebinding(self):
  plan=self.service.prepare(self.fvc_request());path=(Path(self.w.db_path).parent/'production')/'test_ndvi'/'analysis.tif'
  with path.open('ab') as f:f.write(b'changed test bytes')
  with self.assertRaises(GeoError) as error:self.service._verify(plan)
  self.assertEqual(error.exception.code,'AGRICULTURE_SOURCE_CHANGED')
  data=self.dataset([self.polygon(500000,3399970,500030,3400000)]);plan=self.service.prepare({'algorithm_id':'zonal_statistics','source_type':'production','source_task_id':'test_ndvi','dataset_id':data['id']})
  changed=self.w.get_dataset(data['id']);changed['version']=2;self.w.save('dataset',changed)
  with self.assertRaises(GeoError) as error:self.service._verify(plan)
  self.assertEqual(error.exception.code,'AGRICULTURE_DATASET_CHANGED')
 def test_invalid_inputs_no_paths_synthetic_sources_and_nonpolygon_datasets(self):
  with self.assertRaises(GeoError):self.service.prepare({**self.fvc_request(),'url':'https://example.test/a.tif'})
  for source in ('../test_ndvi','https://example.test/a'):
   with self.assertRaises(GeoError):self.service.prepare({**self.fvc_request(),'source_task_id':source})
  data=self.w.create_dataset({'name':'points fixture','geojson':{'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Point','coordinates':[113.,30.]},'properties':{}}]}})
  with self.assertRaises(GeoError):self.service.prepare({'algorithm_id':'zonal_statistics','source_type':'production','source_task_id':'test_ndvi','dataset_id':data['id']})
  t=self.w.get('production_task','test_ndvi');t['plan']['mode']='sample_pixels';self.w.save('production_task',t)
  with self.assertRaises(GeoError):self.service.prepare(self.fvc_request())
 def test_cancellation_retry_and_artifact_whitelist(self):
  gate=threading.Event();original=self.service._read
  def blocked(*args):gate.wait(2);return original(*args)
  with patch.object(self.service,'_read',side_effect=blocked):
   t=self.service.create(self.fvc_request())
   for _ in range(100):
    if self.service.get(t['id'])['progress']>=15:break
    time.sleep(.01)
   self.service.cancel(t['id']);gate.set();done=wait(self.service,t['id']);self.assertEqual(done['status'],'cancelled')
  retry=self.service.retry(t['id']);done=wait(self.service,retry['id']);self.assertEqual(done['status'],'succeeded',done.get('error'));self.assertNotEqual(t['id'],retry['id'])
  with self.assertRaises(GeoError):self.service.artifact(done['id'],'../../workspace.sqlite3')
  self.assertTrue(self.service.artifact(done['id'],'result.geojson').is_file())
 def test_no_valid_values_are_null_and_known_zero_is_preserved(self):
  self.seed_production('missing_source',values=np.full((3,3),np.nan,dtype='float32'))
  t=wait(self.service,self.service.create({**self.fvc_request(),'source_task_id':'missing_source'})['id']);self.assertEqual(t['status'],'succeeded',t.get('error'));self.assertIsNone(t['result']['summary']['mean_fvc']);self.assertEqual(t['result']['summary']['valid_ratio'],0);self.assertIsNone(t['result']['summary']['estimated_vegetation_area_ha'])
 def test_tiny_parcel_inside_source_is_full_but_has_no_native_centres(self):
  data=self.dataset([self.polygon(500001,3399998.8,500001.1,3399998.9)])
  t=wait(self.service,self.service.create({'algorithm_id':'zonal_statistics','source_type':'production','source_task_id':'test_ndvi','dataset_id':data['id']})['id']);self.assertEqual(t['status'],'succeeded',t.get('error'))
  p=t['result']['geojson']['features'][0]['properties'];self.assertEqual(p['agri_coverage_status'],'full');self.assertEqual(p['agri_covered_pixel_count'],0);self.assertFalse(p['agri_pixel_center_hit']);self.assertIsNone(p['agri_mean']);self.assertEqual(t['result']['summary']['no_pixel_hit_zone_count'],1)
 def test_delta_threshold_uses_signed_difference_domain(self):
  self.seed_production('evi2_change',index='evi2',goal='change');data=self.dataset([self.polygon(500000,3399970,500030,3400000)])
  p=self.service.prepare({'algorithm_id':'zonal_statistics','source_type':'production','source_task_id':'evi2_change','dataset_id':data['id'],'field':'delta_index','threshold':3.5});self.assertEqual(p['threshold'],3.5)

if __name__=='__main__':unittest.main()
