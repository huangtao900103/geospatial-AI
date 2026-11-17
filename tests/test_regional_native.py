"""Isolated real GeoTIFF contracts for Atlas reuse, quality and pixel inspection."""
import base64,copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform,transform_bounds,transform_geom
from backend.server import Workspace
from backend.core.geo_services import GeoError
from backend.agriculture.agriculture_services import MAX_CELLS
from backend.analysis.regional_raster import read_window
from test_agriculture_services import wait

class RegionalNativeTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.w=Workspace(self.root/'workspace.sqlite3');self.t=from_origin(700000,3380000,30,30)
  self.values=np.array([[.1,.2,.9],[.4,np.nan,.8],[0,.6,1]],dtype='float32');self.ident='xiantao-2026-spectral-ndvi';self.seed(self.ident,self.values)
 def tearDown(self):self.w.close();self.tmp.cleanup()
 def seed(self,ident,values,inside=None,goal='ndvi'):
  folder=self.root/'agriculture-atlas'/ident;folder.mkdir(parents=True);inside=np.ones(values.shape,dtype='float32') if inside is None else inside.astype('float32');valid=np.where(inside==1,np.isfinite(values),np.nan).astype('float32')
  with rasterio.open(folder/'analysis.tif','w',driver='GTiff',width=values.shape[1],height=values.shape[0],count=3,dtype='float32',crs='EPSG:32649',transform=self.t,nodata=np.nan,compress='deflate') as ds:
   for i,(name,data) in enumerate([('value',values),('valid_mask',valid),('city_mask',inside)],1):ds.write(data,i);ds.set_band_description(i,name)
  (folder/'overlay.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII='));box=list(transform_bounds('EPSG:32649','EPSG:4326',700000,3380000-values.shape[0]*30,700000+values.shape[1]*30,3380000));dates=['2026-09-13T02:00:00Z','2026-10-07T02:00:00Z'] if goal=='change' else ['2026-10-07T02:00:00Z']
  raw={'id':ident,'status':'ready','title':'Isolated native fixture '+goal,'product_type':'city_spectral_reference','scene_ids':['grain'],'scope_label':'Isolated test only','period_label':' → '.join(d[:10] for d in dates),'resolution_m':30,'summary_metrics':[],'summary':{'index_id':'ndvi','operation':'after_minus_before' if goal=='change' else 'single_actual_observation','actual_dates':dates,'reference_year':2026,'boundary_year':2017,'native_crs':'EPSG:32649','valid_ratio':float(np.count_nonzero(valid==1)/inside.sum()),'quality_target_met':True},'provenance':{'source_id':'pc_landsat_c2_l2','boundary':{'bbox':box,'boundary_year_represented':2017},'grid':{'crs':'EPSG:32649'},'selection':{'minimum_city_qa_ratio':.7}},'layers':[{'kind':'raster','image':{'url':'overlay.png','bounds':[[box[1],box[0]],[box[3],box[2]]]},'legend':[]}],'artifacts':['result.json','analysis.tif','overlay.png'],'native_sha256':hashlib.sha256((folder/'analysis.tif').read_bytes()).hexdigest()}
  (folder/'result.json').write_text(json.dumps(raw));return folder,raw
 def dataset(self,left=700000,bottom=3379910,right=700090,top=3380000):
  geo=transform_geom('EPSG:32649','EPSG:4326',{'type':'Polygon','coordinates':[[[left,bottom],[right,bottom],[right,top],[left,top],[left,bottom]]]})
  return self.w.create_dataset({'name':'Isolated analysis region, not a cadastral parcel','geojson':{'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':json.loads(json.dumps(geo))}]}})
 def request(self):return {'algorithm_id':'fvc','source_type':'agriculture_atlas','source_task_id':self.ident,'soil_ndvi':.2,'vegetation_ndvi':.8}
 def test_full_native_fvc_and_original_masks_dates_are_preserved(self):
  task=wait(self.w.agriculture,self.w.agriculture.create(self.request())['id']);self.assertEqual(task['status'],'succeeded',task.get('error'));s=task['result']['summary'];expected=np.clip((self.values.astype('float64')-.2)/.6,0,1);self.assertAlmostEqual(s['mean_fvc'],float(np.nanmean(expected)),places=7);self.assertEqual(s['valid_pixel_count'],8)
  with rasterio.open(self.w.agriculture.artifact(task['id'],'analysis.tif')) as ds:np.testing.assert_allclose(ds.read(1),expected,equal_nan=True,atol=1e-7);self.assertEqual(ds.transform,self.t)
  p=task['result']['provenance'];self.assertFalse(p['statistics_resampled']);self.assertEqual(p['source_ref']['actual_dates']['after'],'2026-10-07T02:00:00Z');self.assertEqual(p['source_window']['width'],3)
 def test_large_registered_source_uses_small_native_zonal_window(self):
  ident='xiantao-2026-spectral-large';self.seed(ident,np.full((1100,1100),.5,dtype='float32'));data=self.dataset(right=700060,bottom=3379940)
  task=wait(self.w.agriculture,self.w.agriculture.create({'algorithm_id':'zonal_statistics','source_type':'agriculture_atlas','source_task_id':ident,'dataset_id':data['id']})['id']);self.assertEqual(task['status'],'succeeded',task.get('error'));p=task['result']['provenance'];self.assertEqual(p['source_full_shape'],[1100,1100]);self.assertLess(p['source_window']['width']*p['source_window']['height'],30);self.assertAlmostEqual(task['result']['summary']['unique_distribution']['mean'],.5);self.assertEqual(MAX_CELLS,1_048_576)
 def test_irregular_city_mask_does_not_claim_bbox_is_full_coverage(self):
  inside=np.ones((3,3),dtype='float32');inside[:,0]=0;ident='xiantao-2026-spectral-hole';self.seed(ident,self.values,inside);data=self.dataset();task=wait(self.w.agriculture,self.w.agriculture.create({'algorithm_id':'zonal_statistics','source_type':'agriculture_atlas','source_task_id':ident,'dataset_id':data['id']})['id']);self.assertEqual(task['status'],'succeeded',task.get('error'));p=task['result']['geojson']['features'][0]['properties'];self.assertEqual(p['agri_coverage_status'],'partial');self.assertIsNone(p['agri_source_coverage_ratio']);self.assertEqual(p['agri_covered_pixel_count'],6)
 def test_delta_source_requires_actual_difference_field_and_cannot_be_fvc(self):
  ident='xiantao-2026-spectral-change';self.seed(ident,self.values,goal='change');data=self.dataset();body={'algorithm_id':'zonal_statistics','source_type':'agriculture_atlas','source_task_id':ident,'dataset_id':data['id']};self.assertEqual(self.w.agriculture.prepare(body)['field'],'delta_index')
  with self.assertRaises(GeoError):self.w.agriculture.prepare({**body,'field':'after_index'})
  with self.assertRaises(GeoError):self.w.agriculture.prepare({**self.request(),'source_task_id':ident})
 def test_catalog_exposes_registered_sources_and_default_delta(self):
  ident='xiantao-2026-spectral-change';self.seed(ident,self.values,goal='change');apps=self.w.applications.catalog()['applications'];fvc=next(v for v in apps if v['id']=='vegetation_cover');zonal=next(v for v in apps if v['id']=='field_zonal_statistics');self.assertIn('agriculture_atlas:'+self.ident,fvc['method']['raster_sources']);self.assertNotIn('agriculture_atlas:'+ident,fvc['method']['raster_sources']);self.assertEqual(zonal['method']['raster_sources']['agriculture_atlas:'+ident]['default_field'],'delta_index')
 def test_exact_pixel_inspection_reads_valid_unknown_and_outside_mask(self):
  p=self.w.agriculture_atlas.get(self.ident);x,y=self.t*(.5,.5);lon,lat=transform('EPSG:32649','EPSG:4326',[x],[y]);r=self.w.inspection_services.inspect('agriculture_atlas',self.ident,lon[0],lat[0],p['product_sha256']);self.assertEqual(r['row'],0);self.assertEqual(r['column'],0);self.assertAlmostEqual(r['value'],.1,places=7);self.assertEqual(r['reference_resolution_m'],30)
  x,y=self.t*(1.5,1.5);lon,lat=transform('EPSG:32649','EPSG:4326',[x],[y]);r=self.w.inspection_services.inspect('agriculture_atlas',self.ident,lon[0],lat[0]);self.assertFalse(r['valid']);self.assertIsNone(r['value'])
  with self.assertRaises(GeoError) as e:self.w.inspection_services.inspect('agriculture_atlas',self.ident,lon[0],lat[0],'f'*64)
  self.assertEqual(e.exception.code,'ATLAS_VERSION_CHANGED')
 def test_metadata_change_invalidates_frozen_reuse_and_pinned_read(self):
  plan=self.w.agriculture.prepare(self.request());path=self.root/'agriculture-atlas'/self.ident/'result.json';raw=json.loads(path.read_text());old=self.w.agriculture_atlas.get(self.ident)['product_sha256'];raw['title']='A new source version';path.write_text(json.dumps(raw))
  with self.assertRaises(GeoError):self.w.agriculture._verify(plan)
  with self.assertRaises(GeoError):self.w.agriculture_atlas.require_version(self.ident,old)
 def test_missing_declared_native_file_is_unavailable_before_download(self):
  (self.root/'agriculture-atlas'/self.ident/'analysis.tif').unlink();r=self.w.agriculture_atlas.list();self.assertEqual(r['products'],[]);self.assertEqual(r['unavailable_product_ids'],[self.ident])
 def test_final_index_support_is_distinct_from_qa_selection_threshold(self):
  path=self.root/'agriculture-atlas'/self.ident/'result.json';raw=json.loads(path.read_text());raw['summary']['valid_ratio']=.2;path.write_text(json.dumps(raw));q=self.w.agriculture_atlas.get(self.ident)['quality'];self.assertEqual(q['status'],'below_target');self.assertEqual(q['valid_ratio'],.2);self.assertIsNone(q['actual_city_qa_ratio']);self.assertIn('公式域',q['warning']);plan=self.w.agriculture.prepare(self.request());self.assertIn(q['warning'],plan['limitations'])
 def test_actual_grid_resolution_mismatch_is_not_resampled(self):
  task,path=self.w.agriculture._source('agriculture_atlas',self.ident);task['plan']['native']['resolution_m']=10
  with self.assertRaises(GeoError):read_window(task,path)
 def test_no_valid_index_support_is_unknown_threshold_area_not_zero(self):
  ident='xiantao-2026-spectral-cloud';self.seed(ident,np.full((3,3),np.nan,dtype='float32'));dataset=self.dataset();task=wait(self.w.agriculture,self.w.agriculture.create({'algorithm_id':'zonal_statistics','source_type':'agriculture_atlas','source_task_id':ident,'dataset_id':dataset['id'],'threshold':.3})['id']);self.assertEqual(task['status'],'succeeded',task.get('error'));p=task['result']['geojson']['features'][0]['properties'];self.assertEqual(p['agri_valid_pixel_count'],0);self.assertIsNone(p['agri_selected_area_ha']);self.assertIsNone(task['result']['summary']['unique_selected_area_ha']);self.assertIsNone(p['agri_mean'])
 def test_regional_budget_does_not_allow_unbounded_window(self):
  task,path=self.w.agriculture._source('agriculture_atlas',self.ident)
  with patch('backend.analysis.regional_raster.MAX_REGIONAL_CELLS',8),self.assertRaises(GeoError) as error:read_window(task,path)
  self.assertEqual(error.exception.code,'AGRICULTURE_REGIONAL_WINDOW_LIMIT')
