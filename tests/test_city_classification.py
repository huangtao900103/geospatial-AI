"""Isolated native-grid math for the city batch path; no production seed data."""
import copy,json,tempfile,unittest
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_geom
from rasterio.features import geometry_mask
from backend.agriculture.city_classification import account_city,select_binding,preview,CLASS_CODES,densify_geometry
from backend.core.geo_services import GeoError

class CityClassificationTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.src=self.root/'input.tif';self.t=from_origin(500000,3370000,10,10)
  self.data=np.asarray([[5,5,1,1,10,0],[5,2,7,4,11,8],[5,2,7,4,11,9],[1,1,1,1,3,0]],dtype='uint8')
  with rasterio.open(self.src,'w',driver='GTiff',width=6,height=4,count=1,dtype='uint8',crs='EPSG:32649',transform=self.t,nodata=0) as s:s.write(self.data,1)
  native={'type':'Polygon','coordinates':[[[500000.1,3369960.1],[500059.9,3369960.1],[500059.9,3369999.9],[500000.1,3369999.9],[500000.1,3369960.1]]]}
  geom=transform_geom('EPSG:32649','EPSG:4326',native)
  self.boundary={'type':'FeatureCollection','features':[{'type':'Feature','geometry':geom,'properties':{}}]};self.meta={'boundary_year_represented':2017};self.binding={'epsg':32649,'item_id':'test-local-only','href':'unused-for-isolated-test'}
 def tearDown(self):self.tmp.cleanup()
 def run_account(self,boundary=None,**kwargs):return account_city(boundary or self.boundary,self.meta,self.root/'out',self.binding,source_path=self.src,**kwargs)
 def test_native_classes_unknown_cloud_and_area_balance(self):
  r=self.run_account();s=r['summary'];self.assertEqual(s['inside_pixel_count'],24);self.assertEqual(s['cropland_pixel_count'],4);self.assertEqual(s['cloud_pixel_count'],1);self.assertEqual(s['unknown_pixel_count'],4);self.assertEqual(s['valid_pixel_count'],20);self.assertAlmostEqual(s['cropland_ha'],.04);self.assertAlmostEqual(s['total_area_ha'],.24);self.assertEqual(sum(c['pixel_count'] for c in r['classes'])+s['unknown_pixel_count'],24)
  with rasterio.open(r['native_path']) as f:self.assertEqual(f.descriptions,('classification','aoi_mask'));np.testing.assert_array_equal(f.read(1),self.data);self.assertTrue((f.read(2)==1).all())
 def test_hole_and_boundary_pixels_never_become_valid_non_crop(self):
  native=transform_geom('EPSG:4326','EPSG:32649',self.boundary['features'][0]['geometry']);native['coordinates'].append([[500009,3369981],[500031,3369981],[500031,3369999],[500009,3369999],[500009,3369981]])
  b=copy.deepcopy(self.boundary);b['features'][0]['geometry']=transform_geom('EPSG:32649','EPSG:4326',native)
  r=self.run_account(b)
  with rasterio.open(r['native_path']) as f:
   mask=f.read(2).astype(bool);self.assertLess(mask.sum(),24);self.assertTrue((f.read(1)[~mask]==0).all());self.assertEqual(int(mask.sum()),r['summary']['inside_pixel_count'])
 def test_too_large_window_not_resampled(self):
  with self.assertRaises(GeoError) as e:self.run_account(max_cells=4)
  self.assertEqual(e.exception.code,'CITY_GRID_LIMIT')
 def test_incomplete_native_source_rejected(self):
  b=json.loads(json.dumps(self.boundary));b['features'][0]['geometry']['coordinates'][0][0][0]-=.01
  with self.assertRaises(GeoError) as e:self.run_account(b)
  self.assertEqual(e.exception.code,'CITY_SOURCE_COVERAGE_GAP')
 def test_unknown_preview_alpha_only_inside_mask(self):
  self.run_account();meta=preview(self.root/'out/classification.tif',self.root/'out')
  from PIL import Image
  unknown=np.asarray(Image.open(self.root/'out/unknown.png'));classes=np.asarray(Image.open(self.root/'out/classes.png'));self.assertTrue((unknown[:,:,3]>0).any());self.assertEqual(meta['statistics_resampled'],False)
  self.assertEqual(unknown.shape,classes.shape)
 def test_densify_preserves_source_and_closure(self):
  source=self.boundary['features'][0]['geometry'];before=copy.deepcopy(source);dense=densify_geometry(source,max_step=.00001);self.assertEqual(source,before);self.assertEqual(dense['coordinates'][0][0],dense['coordinates'][0][-1]);self.assertGreater(len(dense['coordinates'][0]),len(source['coordinates'][0]))

 def test_official_overlap_selects_unique_matching_utm_grid(self):
  def item(zone):
   return {'id':str(zone)+'R-2024','collection':'io-10m-annual-lulc','properties':{'datetime':'2024-01-01T00:00:00Z','end_datetime':'2024-12-31T23:59:59Z'},'assets':{'supercell':{'href':'https://io-10m-annual-lulc.s3.us-west-2.amazonaws.com/'+str(zone)+'R_2024.tif','proj:epsg':32600+zone,'classification:classes':[{'value':5,'name':'crops'},{'value':10,'name':'clouds'}]}}}
  payload={'features':[item(50),item(49)],'links':[]};binding=select_binding({'bbox':[112.9,30.1,113.9,30.6]},payload);self.assertEqual(binding['item_id'],'49R-2024');self.assertEqual(binding['catalog_item_ids'],['50R-2024','49R-2024'])
 def test_cross_zone_city_is_explicitly_rejected(self):
  with self.assertRaises(GeoError) as e:select_binding({'bbox':[113.9,30.1,114.1,30.6]},{'features':[]})
  self.assertEqual(e.exception.code,'CITY_MULTI_UTM_UNSUPPORTED')
