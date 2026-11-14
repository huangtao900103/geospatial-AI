"""HTTP/archive/inspection integration over isolated, explicitly synthetic test rasters."""
import hashlib,io,json,tempfile,threading,time,unittest,zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from unittest.mock import patch
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform
from PIL import Image
from server import Workspace,LocalServer,make_handler
from geo_services import GeoError
from raster_processing_services import files,VERSION
import tests.test_agriculture_services as fixtures

class RasterProcessingIntegrationTest(unittest.TestCase):
 seed_production=fixtures.AgricultureServicesTest.seed_production
 source_bbox=fixtures.AgricultureServicesTest.source_bbox
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.w=Workspace(self.root/'workspace.sqlite3')
  self.transform=from_origin(500000,3400000,10,10);self.values=np.array([[.1,.2,.9],[.4,np.nan,.8],[0,.6,1]],dtype='float32');self.seed_production('test_ndvi')
  self.server=LocalServer(('127.0.0.1',0),make_handler(self.w));self.server.quiet=True;self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.server.server_port)
 def tearDown(self):
  self.w.close();self.server.shutdown();self.server.server_close();self.thread.join(2);self.tmp.cleanup()
 def request(self,path,body=None):
  try:response=urlopen(Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'}),timeout=6)
  except HTTPError as error:response=error
  data=response.read();return response.status,json.loads(data) if 'json' in response.headers.get('Content-Type','') else data
 def await_task(self,id):
  for _ in range(500):
   status,t=self.request('/api/raster-processing/tasks/'+id);self.assertEqual(status,200)
   if t['status'] not in ('queued','running'):return t
   time.sleep(.01)
  self.fail('processing task timeout')
 def create_otsu(self):
  code,t=self.request('/api/raster-processing/tasks',{'algorithm_id':'otsu_segmentation','source_type':'production','source_task_id':'test_ndvi','direction':'above','min_patch_pixels':1});self.assertEqual(code,201,t)
  t=self.await_task(t['id']);self.assertEqual(t['status'],'succeeded',t);return t
 def xy(self,row,col):
  x,y=rasterio.transform.xy(self.transform,row,col);lon,lat=transform('EPSG:32649','EPSG:4326',[x],[y]);return lon[0],lat[0]
 def inspect(self,id,row,col):
  lon,lat=self.xy(row,col);return self.request('/api/map/inspect?kind=raster_processing&task_id='+id+'&lon='+str(lon)+'&lat='+str(lat))
 def check_bundle(self,id,algorithm):
  code,data=self.request('/api/results/raster_processing/'+id+'/bundle.zip');self.assertEqual(code,200)
  with zipfile.ZipFile(io.BytesIO(data)) as z:
   self.assertIsNone(z.testzip());m=json.loads(z.read('manifest.json'))
   self.assertEqual(m['task_ref'],{'type':'raster_processing','task_id':id});self.assertTrue(set(files(algorithm)).issubset(z.namelist()))
   self.assertFalse(any('tianditu' in path or 'sqlite' in path for path in z.namelist()))
   for entry in m['entries']:self.assertEqual(hashlib.sha256(z.read(entry['path'])).hexdigest(),entry['sha256'])
   return z.read('README.txt').decode(),m
 def test_capabilities_health_and_strict_route_validation(self):
  code,c=self.request('/api/raster-processing/capabilities');self.assertEqual(code,200);self.assertEqual(len(c['algorithms']),4);self.assertTrue(all(a['external_engine_call'] is False for a in c['algorithms']))
  code,h=self.request('/api/health');self.assertEqual(code,200);self.assertEqual(h['version'],'0.18.0');self.assertEqual(h['raster_processing_algorithms'],4)
  code,t=self.request('/api/raster-processing/tasks');self.assertEqual((code,t),(200,[]))
  for body in ({'algorithm_id':'arbitrary_script'},{'algorithm_id':'otsu_segmentation','url':'https://example.org/raster.tif'},{'algorithm_id':'otsu_segmentation','source_task_id':'test_ndvi','min_patch_pixels':True}):
   with self.subTest(body=body):self.assertIn(self.request('/api/raster-processing/tasks',body)[0],(400,422))
  self.assertFalse(self.w.raster_processing.list())
 def test_otsu_http_computation_artifacts_bundle_search_and_zero_inspection(self):
  t=self.create_otsu();id=t['id'];r=t['result'];self.assertEqual(r['summary']['valid_pixel_count'],8)
  for name in files('otsu_segmentation'):
   code,data=self.request('/api/raster-processing/tasks/'+id+'/'+name);self.assertEqual(code,200,name);self.assertTrue(data)
  readme,manifest=self.check_bundle(id,'otsu_segmentation');self.assertIn('自动阈值',readme);self.assertIn('未调用OpenGE或Google Earth Engine',readme);self.assertIn('分区并非农田、水体',readme)
  code,v=self.inspect(id,0,0);self.assertEqual(code,200);self.assertTrue(v['valid']);self.assertEqual(v['value'],0.);self.assertEqual(v['value_band'],'selected_mask')
  code,v=self.inspect(id,1,1);self.assertEqual(code,200);self.assertFalse(v['valid']);self.assertIsNone(v['value'])
  code,hits=self.request('/api/map/search?q=Otsu&remote=0');self.assertEqual(code,200)
  items=hits.get('results',hits.get('items',[])) if isinstance(hits,dict) else hits
  hit=next(v for v in items if v.get('task_id')==id);self.assertEqual(hit['result_ref']['type'],'raster_processing');self.assertEqual(hit['result_ref']['url'],'/api/raster-processing/tasks/'+id)
 def seed_temporal(self):
  id='processing_unit_temporal';directory=self.w.raster_processing.output_dir/id;directory.mkdir(parents=True)
  inside=np.ones((3,3),dtype='float32');valid=inside.copy();valid[0,1]=0;values=np.zeros((3,3),dtype='float32');values[0,1]=np.nan;support=np.full((3,3),4,dtype='float32');support[0,1]=2
  bands={'value':values,'valid_observation_count':support,'valid_mask':valid,'aoi_mask':inside}
  with rasterio.open(directory/'analysis.tif','w',driver='GTiff',width=3,height=3,count=4,dtype='float32',crs='EPSG:32649',transform=self.transform,nodata=float('nan')) as ds:
   for n,(key,array) in enumerate(bands.items(),1):ds.write(array,n);ds.set_band_description(n,key)
  Image.new('RGBA',(3,3),(0,0,0,0)).save(directory/'overlay.png');geo={'type':'FeatureCollection','features':[]};(directory/'result.geojson').write_text(json.dumps(geo));(directory/'series.csv').write_text('date,mean_index_all_valid\n2025-10-01,0\n')
  summary={'algorithm_id':'temporal_trend','index_id':'ndvi','scene_count':4,'unique_date_count':4,'minimum_observations':3,'valid_count':8,'native_pixel_count':9,'valid_ratio':8/9,'mean_slope_per_day':0.,'unit':'index_per_day','actual_dates':['2025-10-01T00:00:00Z','2025-10-09T00:00:00Z','2025-10-17T00:00:00Z','2025-10-25T00:00:00Z']}
  result={'summary':summary,'provenance':{'algorithm_id':'temporal_trend','source_type':'public_satellite_pixels','source_id':'pc_sentinel2_l2a','actual_dates':summary['actual_dates'],'bbox':self.source_bbox()},'geojson':geo,'report':{'limitations':['Explicit isolated unit fixture, not a production observation']}}
  (directory/'result.json').write_text(json.dumps(result));task={'id':id,'name':'Unit temporal trend','status':'succeeded','created_at':'2026-11-14','plan':{'algorithm_id':'temporal_trend','index_id':'ndvi','minimum_observations':3,'bbox':self.source_bbox()},'result':result};self.w.save('raster_processing_task',task);return task
 def test_temporal_bundle_dates_support_count_zero_and_unknown_are_separate(self):
  t=self.seed_temporal();readme,m=self.check_bundle(t['id'],'temporal_trend');self.assertEqual(len(m['observation_dates']),4);self.assertIn('指数/天',readme);self.assertIn('共同支持范围',readme)
  code,v=self.inspect(t['id'],0,0);self.assertEqual(code,200);self.assertTrue(v['valid']);self.assertEqual(v['value'],0.);self.assertEqual(v['support_count'],4);self.assertEqual(v['minimum_observations'],3);self.assertIn('指数/天',v['metric_label'])
  code,v=self.inspect(t['id'],0,1);self.assertEqual(code,200);self.assertFalse(v['valid']);self.assertIsNone(v['value']);self.assertEqual(v['support_count'],2);self.assertEqual(v['quality_reason'],'insufficient_observation_support')
 def test_cancel_retry_keep_request_and_reject_extra_fields(self):
  gate=threading.Event();entered=threading.Event()
  def discovery(plan,reader,check):entered.set();gate.wait(2);check();raise GeoError('UNIT_STOP','Isolated test abort')
  body={'algorithm_id':'temporal_mean','source_id':'pc_sentinel2_l2a','index_id':'ndvi','bbox':[114.2,30.5,114.21,30.51],'start':'2025-11-01','end':'2025-11-30','scene_limit':4,'cloud':40}
  with patch('collection_raster.discover_collection',side_effect=discovery):
   code,t=self.request('/api/raster-processing/tasks',body);self.assertEqual(code,201,t);self.assertTrue(entered.wait(2))
   self.assertEqual(self.request('/api/raster-processing/tasks/'+t['id']+'/cancel',{'extra':1})[0],400)
   code,c=self.request('/api/raster-processing/tasks/'+t['id']+'/cancel',{});self.assertEqual(code,200);self.assertEqual(c['status'],'cancelled')
   code,r=self.request('/api/raster-processing/tasks/'+t['id']+'/retry',{});self.assertEqual(code,201,r);self.assertNotEqual(r['id'],t['id']);self.assertEqual(r['plan']['request'],t['plan']['request'])
   self.request('/api/raster-processing/tasks/'+r['id']+'/cancel',{});gate.set()
