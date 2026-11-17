"""Isolated crop product pixels, provenance, unknowns and guarded planning."""
import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import rasterio
from rasterio.transform import from_origin
from backend.agriculture.agriculture_atlas import AgricultureAtlas
from backend.agriculture.crop_reference_services import CropReferenceServices,SOURCE_ID,validate_metadata,prepare_question
from backend.core.inspection_services import InspectionServices
from backend.core.geo_services import GeoError

class CropReferenceTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
  self.store=SimpleNamespace(db_path=str(root/'workspace.sqlite3'),agriculture_atlas=AgricultureAtlas(root/'agriculture-atlas'))
  self.meta={'id':'global_2024_22','collection':'Anthropogenic-V2','properties':{'sci:doi':'10.5281/zenodo.18168302','datetime':'2024-01-01','end_datetime':'2024-12-31'},'assets':{'crop_type_rice_paddies':{'href':'https://s3.waw4-1.cloudferro.com/test/ECDC/Anthropogenic/Anthropogenic_V_global_20240101_20241231_crop_type_rice_paddies.tif','bands':[{'data_type':'uint8','nodata':255}],'classification:classes':[{'value':1,'name':'paddy_rice'}]}}}
  self.grid=from_origin(113.3,30.31,.0001796630568239043,.0001796630568239043)
  self.meta['properties']['proj:transform']=list(self.grid)[:6]
  self.record={'item':self.meta,'collection':{'id':'Anthropogenic-V2','license':'CC-BY-4.0'}}
  self.file=root/'fixture.tif';self.values=np.zeros((32,32),dtype='uint8');self.values[8:11,8:11]=1;self.values[12,12]=255;self.write()
  self.service=CropReferenceServices(self.store,source_path=self.file,metadata_provider=lambda:copy.deepcopy(self.record));self.store.crop_reference=self.service
  self.body={'source_id':SOURCE_ID,'bbox':[113.3005,30.305,113.305,30.3095]}
 def tearDown(self):self.tmp.cleanup()
 def write(self):
  with rasterio.open(self.file,'w',driver='GTiff',width=32,height=32,count=1,dtype='uint8',crs=4326,transform=self.grid,nodata=255) as ds:ds.write(self.values,1)
 def test_real_native_output_and_unknown_not_nonrice(self):
  product,reused=self.service.analyze(self.body);s=product['summary']
  self.assertFalse(reused);self.assertEqual(s['rice_pixels'],9);self.assertGreater(s['unknown_area_ha'],0)
  self.assertAlmostEqual(s['reference_area_ha'],s['valid_area_ha']+s['unknown_area_ha'],places=7)
  self.assertNotAlmostEqual(s['rice_area_ha'],9*.04,places=4)
  self.assertEqual(self.service.analyze(self.body)[1],True)
  with rasterio.open(self.store.agriculture_atlas.artifact(product['id'],'classification.tif')) as ds:self.assertEqual(ds.crs.to_epsg(),4326);self.assertEqual(ds.descriptions,('classification','aoi_mask'))
 def test_class_zero_is_valid_and_255_unknown_in_inspector(self):
  product,_=self.service.analyze(self.body);inspector=InspectionServices(self.store)
  for row,col,valid,expected in [(6,6,True,0),(9,9,True,1),(12,12,False,None)]:
   x,y=rasterio.transform.xy(self.grid,row,col)
   result=inspector.inspect('agriculture_atlas',product['id'],x,y,product['product_sha256'])
   self.assertEqual(result['valid'],valid);self.assertEqual(result['value'],expected)
  with self.assertRaises(GeoError):inspector.inspect('agriculture_atlas',product['id'],x,y,'0'*64)
 def test_all_unknown_stays_null(self):
  self.values[:]=255;self.write();product,_=self.service.analyze(self.body)
  self.assertIsNone(product['summary']['rice_area_ha']);self.assertEqual(product['summary']['valid_ratio'],0)
 def test_valid_zero_rice_is_zero(self):
  self.values[:]=0;self.write();product,_=self.service.analyze(self.body);self.assertEqual(product['summary']['rice_area_ha'],0)
 def test_unregistered_class_rejected(self):
  self.values[9,9]=7;self.write()
  with self.assertRaises(GeoError) as error:self.service.analyze(self.body)
  self.assertEqual(error.exception.code,'CROP_CLASS_CONTRACT');self.assertEqual(self.store.agriculture_atlas.list()['products'],[])
 def test_source_and_license_are_bound(self):
  for field,value in [('license','unknown'),('id','other')]:
   bad=copy.deepcopy(self.record);bad['collection'][field]=value
   with self.assertRaises(GeoError):validate_metadata(bad['item'],bad['collection'])
  bad=copy.deepcopy(self.meta);bad['assets']['crop_type_rice_paddies']['href']='https://example.org/arbitrary.tif'
  with self.assertRaises(GeoError):validate_metadata(bad,self.record['collection'])
 def test_arbitrary_paths_and_large_aoi_rejected(self):
  for change in [{'path':'/tmp/x'},{'bbox':[113,30,114,31]},{'scope':'province'},{'source_id':'earice10_2023'},{'scope':'xiantao_city'}]:
   with self.assertRaises(GeoError):self.service.prepare({**self.body,**change})
 def test_planner_never_changes_year_scope_or_source(self):
  good=prepare_question(self.store,'查看仙桃全市2024年水稻面积',{})
  self.assertTrue(good['executable']);self.assertEqual(good['tool']['inputs']['scope'],'xiantao_city')
  for question,context in [('查看仙桃全市2026年水稻面积',{}),('查看武汉全市2024年水稻面积',{}),('查看仙桃全市2024年5月水稻面积',{}),('查看仙桃全市2024年水稻面积',{'source_id':'pc_landsat_c2_l2'})]:self.assertFalse(prepare_question(self.store,question,context)['executable'])
  for question in ['统计仙桃全市2024年水稻和油菜面积','统计仙桃全市2024年水稻NDVI分布','统计仙桃全市2024年水稻地块数量','统计仙桃全市2024年水稻面积占比']:
   self.assertFalse(prepare_question(self.store,question,{})['executable'])
  self.assertTrue(prepare_question(self.store,'查询当前选区2024年水稻面积',{'bbox':self.body['bbox']})['executable'])
 def test_cancellation_does_not_publish(self):
  def cancel():raise GeoError('EXECUTION_STOPPED','isolated cancel')
  with self.assertRaises(GeoError):self.service.analyze(self.body,cancel)
  self.assertEqual(self.store.agriculture_atlas.list()['products'],[])
 def test_native_read_and_archive_reuse_record_different_evidence(self):
  rows=[];self.store.save=lambda kind,row:rows.append((kind,copy.deepcopy(row)))
  self.service.analyze(self.body);self.service.analyze(self.body)
  self.assertEqual([row[1]['verification']['operation'] for row in rows],['native_crop_read','verified_native_crop_archive_read'])
  self.assertTrue(all(row[0]=='data_source_check' and row[1]['verification']['status']=='succeeded' for row in rows))
 def admin_provider(self,name='天门市',code='156429006'):
  geo=self.service.prepare(self.body)['boundary']
  item={'name':name,'code':code,'source':'isolated administrative fixture','source_url':'https://lbs.tianditu.gov.cn/server/administrative2.html','fetched_at':'2026-11-16','geojson':geo,'boundary_status':'available'}
  calls=[]
  self.store.tianditu=SimpleNamespace(administrative=lambda query:(calls.append(query) or {'results':[copy.deepcopy(item)]}))
  return item,calls
 def test_other_city_uses_own_frozen_boundary_and_shared_pixels(self):
  item,calls=self.admin_provider();body={'source_id':SOURCE_ID,'scope':'administrative','region':'天门市'}
  product,_=self.service.analyze(body)
  self.assertIn('天门市',product['title']);self.assertNotIn('仙桃',product['scope_label'])
  self.assertEqual(product['provenance']['region'],{'name':'天门市','code':'156429006'})
  self.assertIsNone(product['provenance']['boundary_source']['boundary_year_represented'])
  self.assertEqual(product['summary']['rice_pixels'],9)
  self.assertTrue(self.service.analyze(body)[1]);self.assertEqual(calls,['天门市'])
  from backend.agriculture.regional_product_tools import compatibility_error
  self.assertIsNone(compatibility_error(product,'下载天门市2024年水稻分析结果',{}))
  self.assertIsNotNone(compatibility_error(product,'下载潜江市2024年水稻分析结果',{}))
  self.assertIsNotNone(compatibility_error(product,'下载结果',{'bbox':self.body['bbox']}))
 def test_admin_unknown_ambiguous_and_point_only_never_use_a_rectangle(self):
  item,_=self.admin_provider();body={'source_id':SOURCE_ID,'scope':'administrative','region':'潜江市'}
  with self.assertRaises(GeoError):self.service.prepare(body)
  item['name']='潜江市';item['geojson']=None;item['boundary_status']='unavailable'
  self.store.tianditu.administrative=lambda query:{'results':[item]}
  with self.assertRaises(GeoError):self.service.prepare(body)
  item['geojson']=self.service.prepare(self.body)['boundary'];item['boundary_status']='available'
  self.store.tianditu.administrative=lambda query:{'results':[item,copy.deepcopy(item)]}
  with self.assertRaises(GeoError):self.service.prepare(body)
 def test_admin_region_codes_conflicts_and_boundary_hash(self):
  self.admin_provider();body={'source_id':SOURCE_ID,'scope':'administrative','region':'429006'}
  self.assertEqual(self.service.prepare(body)['boundary_source']['name'],'天门市')
  with self.assertRaises(GeoError):self.service.prepare({**body,'bbox':self.body['bbox']})
  with self.assertRaises(GeoError):self.service.prepare({**self.body,'region':'天门市'})
  cache=next((self.service.root/'boundaries').glob('*.json'));doc=json.loads(cache.read_text());doc['item']['code']='other';cache.write_text(json.dumps(doc))
  with self.assertRaises(GeoError):self.service.prepare(body)
 def test_city_question_scope_and_selected_region_are_not_interchanged(self):
  self.admin_provider()
  good=prepare_question(self.store,'统计天门全市2024年水稻分布参考面积',{})
  self.assertTrue(good['executable']);self.assertEqual(good['tool']['inputs']['region'],'天门市')
  self.assertFalse(prepare_question(self.store,'统计天门市和潜江市2024年水稻面积',{})['executable'])
  self.assertFalse(prepare_question(self.store,'统计天门市2024年水稻面积',{'region':'仙桃市'})['executable'])
  self.assertTrue(prepare_question(self.store,'查询选定区域2024年水稻面积',{'bbox':self.body['bbox']})['executable'])
 def test_large_admin_is_rejected_before_raster_read(self):
  item,_=self.admin_provider();item['geojson']={'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Polygon','coordinates':[[[110,30],[115,30],[115,35],[110,35],[110,30]]]},'properties':{}}]}
  self.store.tianditu.administrative=lambda query:{'results':[item]}
  with self.assertRaises(GeoError) as error:self.service.prepare({'source_id':SOURCE_ID,'scope':'administrative','region':'天门市'})
  self.assertEqual(error.exception.code,'CROP_PIXEL_LIMIT')

if __name__=='__main__':unittest.main()
