"""Offline protocol/numeric fixtures; live pixel evidence is recorded separately."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from importlib.util import find_spec
from urllib.parse import parse_qs,urlsplit
from backend.core.geo_services import GeoError
from backend.analysis.landsat_reader import LandsatReader,specification,asset_url,QUALITY_POLICY

BASE='https://landsateuwest.blob.core.windows.net/landsat-c2/level-2/standard/oli-tirs/2025/123/039/LC09_TEST/'
READY=all(find_spec(x) for x in ('numpy','rasterio','PIL'))
def plan(index='ndvi',goal='ndvi'):
 return {'source_id':'pc_landsat_c2_l2','mode':'sentinel_cog','sampling':'native','index_id':index,'index':specification(index),'requirement':'offline synthetic Landsat protocol fixture','goal':goal,'aoi':{'bbox':[114.2,30.5,114.21,30.51]},'cloud':30,'periods':{'after':['2025-11-01','2025-11-30'],'before':['2025-10-01','2025-10-31']},'direction':'absolute','threshold':.3,'selection':{'operator':'gt'},'limitations':specification(index)['limitations']}
def scene():
 assets={}
 for key,band in [('green','SR_B3'),('red','SR_B4'),('nir08','SR_B5'),('swir16','SR_B6'),('swir22','SR_B7')]:assets[key]={'href':BASE+band+'.TIF','raster:bands':[{'scale':.0000275,'offset':-.2,'nodata':0,'spatial_resolution':30}]}
 for key in ('qa_pixel','qa_radsat'):assets[key]={'href':BASE+key.upper()+'.TIF'}
 return {'type':'Feature','id':'LC09_L2SP_123039_20251129_02_T1','collection':'landsat-c2-l2','bbox':[112,29,115,32],'properties':{'platform':'landsat-9','datetime':'2025-11-29T02:56:40Z','eo:cloud_cover':.17},'assets':assets}

class LandsatProtocolTest(unittest.TestCase):
 def reader(self,features=None):
  reader=LandsatReader();reader.fetch_json=Mock(return_value={'features':features if features is not None else [scene()]});return reader
 def test_six_indices_use_landsat_bands_and_30m(self):
  for id in ('ndvi','ndwi','mndwi','ndmi','ndbi','nbr'):
   s=specification(id);self.assertEqual(s['reference_resolution_m'],30);self.assertTrue(all(b['native_resolution_m']==30 and b['id'].startswith('SR_B') for b in s['required_bands']));self.assertTrue(s['reference_band'].startswith('SR_B'));self.assertFalse(any(s.startswith('SCL') for s in s['limitations']))
  self.assertEqual([b['id'] for b in specification('ndvi')['required_bands']],['SR_B5','SR_B4'])
 def test_only_fixed_unsigned_oli_assets_are_allowed(self):
  self.assertEqual(asset_url(BASE+'SR_B4.TIF'),BASE+'SR_B4.TIF')
  for value in ['http://'+BASE[8:]+'a.TIF',BASE+'a.TIF?sig=secret',BASE+'a.TIF#x',BASE+'../a.TIF',BASE+'%2e%2e/a.TIF',BASE.replace('landsateuwest','other')+'a.TIF',BASE.replace('https://','https://user@')+'a.TIF','https://evil.invalid/a.tif',None]:
   with self.subTest(value=value),self.assertRaises(GeoError):asset_url(value)
 def test_actual_asset_metadata_controls_calibration_and_no_xml_is_invented(self):
  r=self.reader();d=r.discover(plan(),lambda:None)['after'];self.assertEqual(d['assets']['nir']['source_band'],'SR_B5');self.assertEqual(d['assets']['red']['offset'],-.2);self.assertNotIn('scl',d['assets']);self.assertIn('stac_item_sha256',d['calibration']);self.assertNotIn('xml_sha256',d['calibration'])
  self.assertEqual(set(d['assets']),{'red','nir','qa_pixel','qa_radsat'})
 def test_missing_and_unexpected_calibration_never_guess_s2_scale(self):
  for field,value in [('scale',.0001),('offset',0),('nodata',None),('spatial_resolution',10)]:
   f=scene();f['assets']['red']['raster:bands'][0][field]=value
   with self.subTest(field=field),self.assertRaises(GeoError):self.reader([f]).discover(plan(),lambda:None)
  f=scene();f['assets']['red'].pop('raster:bands')
  with self.assertRaises(GeoError):self.reader([f]).discover(plan(),lambda:None)
 def test_sensor_date_cloud_coverage_and_pinned_id_are_hard_filters(self):
  for change in ('platform','date','cloud','bbox'):
   f=scene()
   if change=='platform':f['properties']['platform']='landsat-7'
   if change=='date':f['properties']['datetime']='2024-11-29T00:00:00Z'
   if change=='cloud':f['properties']['eo:cloud_cover']=99
   if change=='bbox':f['bbox']=[113,29,114,30]
   with self.subTest(change=change),self.assertRaises(GeoError):self.reader([f]).discover(plan(),lambda:None)
  p=plan();p['scene_id']='LC08_L2SP_123039_20251121_02_T1'
  with self.assertRaises(GeoError):self.reader().discover(p,lambda:None)
  p['scene_id']=scene()['id'];r=self.reader();self.assertEqual(r.discover(p,lambda:None)['after']['id'],p['scene_id']);self.assertEqual(parse_qs(urlsplit(r.fetch_json.call_args[0][0]).query)['ids'],[p['scene_id']])
  p['goal']='change'
  with self.assertRaises(GeoError):r.discover(p,lambda:None)
 def test_no_representative_grid_fallback(self):
  with self.assertRaises(GeoError):self.reader().read(plan(),{},lambda:None)
 def test_sas_only_in_memory_not_scene_bindings(self):
  r=LandsatReader();r.fetch_json=Mock(return_value={'token':'sig=offline-test-secret'});url=r.signed_url(BASE+'SR_B4.TIF',lambda:None);self.assertIn('?sig=',url);self.assertEqual(r.fetch_json.call_count,1);r.signed_url(BASE+'SR_B5.TIF',lambda:None);self.assertEqual(r.fetch_json.call_count,1)
  d=self.reader().discover(plan(),lambda:None);self.assertNotIn('offline-test-secret',json.dumps(d))

@unittest.skipUnless(READY,'raster runtime required')
class LandsatNativeTest(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
 def tearDown(self):self.temp.cleanup()
 def fixture(self,index='ndvi'):
  import numpy as np
  from affine import Affine
  from rasterio.transform import array_bounds
  from rasterio.warp import transform_bounds
  p=plan(index);transform=Affine(30,0,420000,0,-30,3380000);p['aoi']['bbox']=list(transform_bounds('EPSG:32650','EPSG:4326',*array_bounds(2,4,transform),densify_pts=21))
  b={'after':{'red':np.full((2,4),.1,dtype='float32'),'nir':np.full((2,4),.2,dtype='float32'),'green':np.full((2,4),.15,dtype='float32'),'swir1':np.full((2,4),.05,dtype='float32'),'swir2':np.full((2,4),.03,dtype='float32'),'qa_pixel':np.full((2,4),64,dtype='float32'),'qa_radsat':np.zeros((2,4),dtype='float32')},'_mask':np.ones((2,4),dtype='bool'),'metadata':{'quality_kind':'landsat_qa','quality_policy':QUALITY_POLICY,'sensor':'landsat-8/9-oli','reader_version':'landsat-c2-l2-oli-1.0','source_type':'public_satellite_pixels','provider':'offline synthetic arrays, not remote data','crs':'EPSG:32650','transform':list(transform)[:6],'cell_area_m2':900.,'input_sha256':'offline-fixture','resampling':'nearest QA/reference','scenes':{'after':{'id':'offline'}}}}
  return p,b
 def test_qa_bits_water_and_saturation_masks(self):
  import numpy as np
  from backend.analysis.native_raster import landsat_quality
  q=np.array([[64,128,0]+[64|(1<<i) for i in range(6)]],dtype='float32');rad=np.zeros_like(q);epoch={'qa_pixel':q,'qa_radsat':rad};finite,good=landsat_quality(epoch);self.assertTrue(finite.all());np.testing.assert_array_equal(good,[[True,True,True,False,False,False,False,False,False]])
  rad[0,0]=2048;rad[0,1]=1;self.assertFalse(landsat_quality(epoch)[1][0,:2].any())
 def test_fractional_nan_out_of_range_qa_is_invalid(self):
  import numpy as np
  from backend.analysis.native_raster import landsat_quality
  a=np.array([[float('nan'),64.5,-1,65536,64]],dtype='float32');finite,good=landsat_quality({'qa_pixel':a,'qa_radsat':np.zeros_like(a)});self.assertEqual(int((finite&good).sum()),1)
 def test_quality_and_negative_reflectance_are_separate_reasons(self):
  from backend.analysis.native_raster import grids,quality_summary
  p,b=self.fixture();b['after']['qa_pixel'][0,0]=8;b['after']['qa_radsat'][0,1]=1;b['after']['red'][0,2]=-.01
  data,reasons=grids(b);self.assertEqual(int(data['after'][1].sum()),5);self.assertEqual(reasons['after']['qa_excluded'],2);self.assertEqual(reasons['after']['invalid_reflectance'],1);self.assertNotIn('scl_excluded',reasons['after']);self.assertEqual(quality_summary(b)['after']['quality_kind'],'landsat_qa')
 def test_export_preserves_calibrated_inputs_actual_qa_and_30m(self):
  import numpy as np,rasterio
  from backend.analysis.native_raster import compute_native
  p,b=self.fixture();b['after']['qa_pixel'][0,0]=8;r=compute_native(p,b,'test-ls',self.root,lambda:None)
  self.assertAlmostEqual(r['summary']['mean_index'],1/3,places=6);self.assertEqual(r['summary']['valid_count'],7);self.assertEqual(r['provenance']['grid']['reference_resolution_m'],30);self.assertIn('Landsat',r['report']['title']);self.assertNotIn('scl_retained',r['provenance']['quality'])
  with rasterio.open(self.root/'analysis.tif') as ds:
   self.assertEqual(ds.res,(30.,30.));self.assertEqual(ds.count,7);self.assertEqual(ds.descriptions,('after_ndvi','common_valid_mask','aoi_mask','after_nir_reflectance','after_red_reflectance','after_qa_pixel','after_qa_radsat'));np.testing.assert_array_equal(ds.read(6),b['after']['qa_pixel']);self.assertNotIn('after_scl',ds.descriptions)
 def test_read_native_retains_valid_zero_qa_even_if_geotiff_nodata_is_zero(self):
  import numpy as np,rasterio
  from affine import Affine
  from rasterio.transform import array_bounds
  from rasterio.warp import transform_bounds
  from backend.analysis.native_raster import quality_summary
  transform=Affine(30,0,420000,0,-30,3380000);p=plan();p['aoi']['bbox']=list(transform_bounds('EPSG:32650','EPSG:4326',*array_bounds(4,4,transform),densify_pts=21));paths={};assets={}
  for band,value in [('red',10000),('nir',15000),('qa_pixel',64),('qa_radsat',0)]:
   path=self.root/(band+'.tif');paths[BASE+band+'.TIF']=str(path)
   with rasterio.open(path,'w',driver='GTiff',width=4,height=4,count=1,dtype='uint16',crs='EPSG:32650',transform=transform,nodata=0) as ds:ds.write(np.full((4,4),value,dtype='uint16'),1)
   assets[band]={'href':BASE+band+'.TIF','scale':1 if band.startswith('qa_') else .0000275,'offset':0 if band.startswith('qa_') else -.2,'nodata':None if band.startswith('qa_') else 0,'raw_quality':band.startswith('qa_')}
  reader=LandsatReader();reader.signed_url=lambda url,check:paths[url];reader.provider='offline georeferenced fixture, not satellite acquisition'
  bands=reader.read_native(p,{'after':{'assets':assets,'id':'offline','datetime':'2025-11-01T00:00:00Z'}},lambda:None);self.assertEqual(quality_summary(bands)['after']['valid_count'],16);self.assertEqual(bands['metadata']['reference_resolution_m'],30)
  # A product's nominal STAC spacing cannot override a contradictory file grid.
  with rasterio.open(paths[BASE+'nir.TIF'],'w',driver='GTiff',width=4,height=4,count=1,dtype='uint16',crs='EPSG:32650',transform=Affine(15,0,420000,0,-15,3380000),nodata=0) as ds:ds.write(np.full((4,4),15000,dtype='uint16'),1)
  with self.assertRaises(GeoError) as error:reader.read_native(p,{'after':{'assets':assets,'id':'offline','datetime':'2025-11-01T00:00:00Z'}},lambda:None)
  self.assertEqual(error.exception.code,'LANDSAT_GRID_UNSUPPORTED')

if __name__=='__main__':unittest.main()
