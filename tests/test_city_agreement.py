"""Independent math and file checks for cross-product screening, never change labels."""
import copy,csv,json,tempfile,unittest
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin,from_bounds
from rasterio.warp import transform_bounds,reproject,Resampling
from backend.agriculture.city_cropland_agreement import classify,account,make_preview,atlas_fields,audit_files
from backend.agriculture.city_classification import file_sha
from backend.core.geo_services import GeoError
class CityAgreementTest(unittest.TestCase):
 def test_four_reference_classes_and_unknown_not_neither(self):
  io=np.array([[5,5,7,7,10,5,0,5]],dtype='uint8');wc=np.array([[40,50,40,50,40,0,40,40]],dtype='uint8');inside=np.array([[1,1,1,1,1,1,1,0]],dtype='bool');before=(io.copy(),wc.copy());result,valid=classify(io,wc,inside);np.testing.assert_array_equal(result,[[1,2,3,4,0,0,0,0]]);self.assertEqual(int(valid.sum()),4);np.testing.assert_array_equal(io,before[0]);np.testing.assert_array_equal(wc,before[1])
 def test_unregistered_class_values_are_unknown(self):
  io=np.array([[6,5,5]],dtype='uint8');wc=np.array([[40,255,40]],dtype='uint8');r,valid=classify(io,wc,np.ones(io.shape,dtype='bool'));np.testing.assert_array_equal(r,[[0,0,1]])
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name);self.io=self.p/'io.tif';self.wc=self.p/'wc.tif';self.t=from_origin(500000,3370000,10,10);data=np.array([[5,5,7,7],[5,10,7,7],[5,5,7,7],[5,5,7,7]],dtype='uint8');mask=np.ones(data.shape,dtype='uint8');mask[3,3]=0
  with rasterio.open(self.io,'w',driver='GTiff',height=4,width=4,count=2,dtype='uint8',crs='EPSG:32649',transform=self.t,nodata=0) as f:
   f.write(data,1);f.write(mask,2);f.set_band_description(1,'classification');f.set_band_description(2,'aoi_mask')
  bb=transform_bounds('EPSG:32649','EPSG:4326',500000,3369960,500040,3370000,densify_pts=21);pad=.0003;bb=[bb[0]-pad,bb[1]-pad,bb[2]+pad,bb[3]+pad];self.wct=from_bounds(*bb,20,20)
  wdata=np.full((20,20),40,dtype='uint8');wdata[:,:10]=50
  with rasterio.open(self.wc,'w',driver='GTiff',height=20,width=20,count=1,dtype='uint8',crs='EPSG:4326',transform=self.wct,nodata=0) as f:f.write(wdata,1)
 def tearDown(self):self.tmp.cleanup()
 def test_common_grid_counts_independent_reprojection_and_input_integrity(self):
  hashes=[file_sha(self.io),file_sha(self.wc)];r=account(self.io,self.wc,self.p/'out')
  with rasterio.open(self.io) as i,rasterio.open(self.wc) as w:
   aligned=np.zeros((4,4),dtype='uint8');reproject(source=w.read(1),destination=aligned,src_transform=w.transform,src_crs=w.crs,dst_transform=i.transform,dst_crs=i.crs,src_nodata=0,dst_nodata=0,resampling=Resampling.nearest)
   raw=i.read(1);inside=i.read(2)==1;expected,valid=classify(raw,aligned,inside)
  with rasterio.open(self.p/'out/analysis.tif') as f:
   self.assertEqual(f.count,5);np.testing.assert_array_equal(f.read(1),expected);np.testing.assert_array_equal(f.read(2),inside);np.testing.assert_array_equal(f.read(5),valid)
  self.assertEqual(r['summary']['inside_pixel_count'],15);self.assertEqual(r['summary']['unknown_pixel_count'],1);self.assertFalse(r['summary']['is_change_detection']);self.assertFalse(r['summary']['is_accuracy_assessment']);self.assertTrue(r['summary']['worldcover_resampled_for_comparison']);self.assertEqual(hashes,[file_sha(self.io),file_sha(self.wc)])
  for c in r['classes']:self.assertEqual(c['pixel_count'],int((expected==c['code']).sum()));self.assertAlmostEqual(c['area_ha'],c['pixel_count']*.01)
 def test_row_csv_balance(self):
  r=account(self.io,self.wc,self.p/'out')
  with (self.p/'out/row_counts.csv').open(encoding='utf-8-sig') as f:rows=list(csv.DictReader(f))
  self.assertEqual(sum(int(x['inside_pixels']) for x in rows),r['summary']['inside_pixel_count']);self.assertEqual(sum(int(x['unknown_pixels']) for x in rows),r['summary']['unknown_pixel_count'])
  for c in r['classes']:self.assertEqual(sum(int(x['class_'+str(c['code'])]) for x in rows),c['pixel_count'])
 def test_wrong_reference_grid_rejected(self):
  with self.assertRaises(GeoError):account(self.wc,self.wc,self.p/'out')
 def test_preview_retains_comparison_resampling_disclosure(self):
  account(self.io,self.wc,self.p/'out');m=make_preview(self.p/'out');self.assertTrue(m['statistics_resampled']);self.assertTrue(m['worldcover_resampled_for_comparison']);self.assertFalse(m['reference_grid_resampled']);self.assertEqual(len(m['legend']),5)

 def test_atlas_contract_has_explicit_cross_product_scope_and_bounds(self):
  r=account(self.io,self.wc,self.p/'out');image=make_preview(self.p/'out');r=atlas_fields(r,{'type':'FeatureCollection','features':[]},image)
  self.assertEqual(r['schema_version'],'agriculture-atlas-1.0');self.assertEqual(r['product_type'],'cross_product_review');self.assertEqual(r['scene_ids'],['farmland','high_standard','grain']);self.assertEqual(len(r['summary_metrics']),4);self.assertEqual(r['layers'][0]['image']['bounds'],[[image['bounds'][1],image['bounds'][0]],[image['bounds'][3],image['bounds'][2]]]);self.assertTrue(r['layers'][1]['boundary'])

 def test_nearest_alignment_matches_exact_reference_pixel_centers(self):
  from rasterio.warp import transform
  width,height=2000,80;t=from_origin(500000,3370000,10,10);io=self.p/'wide-io.tif';wc=self.p/'wide-wc.tif'
  with rasterio.open(io,'w',driver='GTiff',width=width,height=height,count=2,dtype='uint8',crs='EPSG:32649',transform=t,nodata=0) as f:
   f.write(np.full((height,width),5,dtype='uint8'),1);f.write(np.ones((height,width),dtype='uint8'),2);f.set_band_description(1,'classification');f.set_band_description(2,'aoi_mask')
  bb=transform_bounds('EPSG:32649','EPSG:4326',500000,3370000-height*10,500000+width*10,3370000,densify_pts=41);rx=1/12000;left=bb[0]-.001;top=bb[3]+.001;ww=int(np.ceil((bb[2]-left+.001)/rx));hh=int(np.ceil((top-bb[1]+.001)/rx));wt=from_origin(left,top,rx,rx);grid=np.indices((hh,ww)).sum(axis=0);values=np.where(grid%2,40,50).astype('uint8')
  with rasterio.open(wc,'w',driver='GTiff',width=ww,height=hh,count=1,dtype='uint8',crs='EPSG:4326',transform=wt,nodata=0) as f:f.write(values,1)
  result=account(io,wc,self.p/'wide-out');rr,cc=np.indices((height,width));x=t.c+(cc+.5)*10;y=t.f-(rr+.5)*10;lon,lat=transform('EPSG:32649','EPSG:4326',x.ravel().tolist(),y.ravel().tolist());columns=np.floor((np.array(lon)-wt.c)/wt.a).astype(int);rows=np.floor((np.array(lat)-wt.f)/wt.e).astype(int);expected=values[rows,columns].reshape((height,width))
  with rasterio.open(self.p/'wide-out/analysis.tif') as f:np.testing.assert_array_equal(f.read(4),expected)
  self.assertEqual(result['summary']['transformer_tolerance_pixels'],1e-9)

 def test_independent_audit_recounts_bands_csv_and_original_source(self):
  r=account(self.io,self.wc,self.p/'out');v=audit_files(self.p/'out',self.io,self.wc,r['summary'],r['source_sha256']);self.assertTrue(v['per_pixel_five_band_recomputation']);self.assertTrue(v['wc_nearest_alignment_values_match']);self.assertTrue(v['source_files_unchanged']);self.assertGreater(v['independent_wc_source_center_samples'],0)
