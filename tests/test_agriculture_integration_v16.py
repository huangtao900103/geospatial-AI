"""Real HTTP and archive integration over temporary, explicit fixture rasters."""
import io
import json
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from unittest.mock import patch
import numpy as np
from rasterio.transform import from_origin
from rasterio.warp import transform
from server import Workspace,LocalServer,make_handler
import tests.test_agriculture_services as fixtures


class AgricultureIntegrationTest(unittest.TestCase):
 seed_production=fixtures.AgricultureServicesTest.seed_production
 source_bbox=fixtures.AgricultureServicesTest.source_bbox
 def polygon(self,*args):
  return json.loads(json.dumps(fixtures.AgricultureServicesTest.polygon(self,*args)))
 dataset=fixtures.AgricultureServicesTest.dataset
 fvc_request=fixtures.AgricultureServicesTest.fvc_request
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.w=Workspace(self.root/'workspace.sqlite3');self.service=self.w.agriculture
  self.transform=from_origin(500000,3400000,10,10);self.values=np.array([[.1,.2,.9],[.4,np.nan,.8],[0.,.6,1.]],dtype='float32');self.seed_production('test_ndvi')
  self.server=LocalServer(('127.0.0.1',0),make_handler(self.w));self.server.quiet=True;self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.server.server_port)
 def tearDown(self):
  self.w.close();self.server.shutdown();self.server.server_close();self.thread.join(2);self.temp.cleanup()
 def request(self,path,body=None):
  req=Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
  try:response=urlopen(req,timeout=5)
  except HTTPError as error:response=error
  raw=response.read();return response.status,json.loads(raw) if 'json' in response.headers.get('Content-Type','') else raw
 def completed(self,body):
  status,task=self.request('/api/agriculture/tasks',body);self.assertEqual(status,201,task)
  for _ in range(300):
   status,current=self.request('/api/agriculture/tasks/'+task['id']);self.assertEqual(status,200)
   if current['status'] not in ('queued','running'):self.assertEqual(current['status'],'succeeded',current);return current
   time.sleep(.01)
  self.fail('agriculture HTTP task did not finish')
 def bundle(self,task):
  status,manifest=self.request('/api/results/agriculture/'+task['id']+'/manifest');self.assertEqual(status,200,manifest)
  status,raw=self.request('/api/results/agriculture/'+task['id']+'/bundle.zip');self.assertEqual(status,200)
  archive=zipfile.ZipFile(io.BytesIO(raw));self.assertIsNone(archive.testzip());self.assertEqual(json.loads(archive.read('manifest.json')),manifest)
  return manifest,archive
 def test_health_capabilities_and_invalid_inputs_use_real_service(self):
  status,health=self.request('/api/health');self.assertEqual(status,200);self.assertEqual(health['version'],'0.18.0');self.assertEqual(health['agriculture_algorithms'],2)
  status,caps=self.request('/api/agriculture/capabilities');self.assertEqual(status,200);self.assertEqual({a['id'] for a in caps['algorithms']},{'fvc','zonal_statistics'})
  self.assertEqual(self.request('/api/agriculture/tasks')[1],[])
  self.assertEqual(self.request('/api/agriculture/tasks',{**self.fvc_request(),'url':'https://invalid.test/a.tif'})[0],400)
  self.assertEqual(self.request('/api/agriculture/tasks',{**self.fvc_request(),'soil_ndvi':None})[0],422)
  self.assertEqual(self.request('/api/agriculture/tasks/missing')[0],404)
 def test_fvc_http_artifacts_bundle_readme_search_and_native_point_inspection(self):
  task=self.completed(self.fvc_request());prefix='/api/agriculture/tasks/'+task['id']
  self.assertEqual(len(self.request('/api/agriculture/tasks')[1]),1)
  names=('analysis.tif','overlay.png','result.geojson','result.json','summary.csv')
  for name in names:self.assertEqual(self.request(prefix+'/'+name)[0],200,name)
  self.assertEqual(self.request(prefix+'/zones.csv')[0],404)
  (self.service.output_dir/task['id']/'private-config.json').write_text('fixture private data')
  manifest,archive=self.bundle(task)
  with archive:
   archived={e['path'] for e in manifest['entries'] if e['role']=='archived_artifact'};self.assertEqual(archived,set(names));self.assertNotIn('private-config.json',archive.namelist())
   readme=archive.read('README.txt').decode('utf-8')
   for text in ('FVC估算','0.2 / 0.8','未做本地校准','非耕地分类面积或实测面积','2025-11-14T02:30:00Z','源栅格SHA256'):
    self.assertIn(text,readme)
  self.assertEqual(manifest['observation_dates'],{'after':'2025-11-14T02:30:00Z'})
  status,search=self.request('/api/map/search?q=FVC&remote=0');self.assertEqual(status,200)
  hit=next(h for h in search['results'] if h.get('task_id')==task['id']);self.assertEqual(hit['task_type'],'agriculture');self.assertEqual(hit['result_ref']['image_kind'],'fvc');self.assertEqual(hit['result_ref']['url'],prefix)
  lon,lat=transform('EPSG:32649','EPSG:4326',[500005],[3399995])
  status,point=self.request('/api/map/inspect?kind=agriculture&task_id='+task['id']+'&lon='+str(lon[0])+'&lat='+str(lat[0]));self.assertEqual(status,200,point)
  self.assertTrue(point['valid']);self.assertEqual(point['value'],0);self.assertEqual(point['value_band'],'fvc');self.assertEqual(point['reference_resolution_m'],10);self.assertIn('fvc_unclamped',point['bands']);self.assertEqual(point['actual_dates'],['2025-11-14'])
  lon,lat=transform('EPSG:32649','EPSG:4326',[500015],[3399985])
  status,point=self.request('/api/map/inspect?kind=agriculture&task_id='+task['id']+'&lon='+str(lon[0])+'&lat='+str(lat[0]));self.assertEqual(status,200);self.assertFalse(point['valid']);self.assertIsNone(point['value'])
 def test_zonal_http_bundle_only_has_three_artifacts_and_no_raster_inspection(self):
  data=self.dataset([self.polygon(500000,3399970,500030,3400000),self.polygon(499990,3399980,500010,3400010)])
  task=self.completed({'algorithm_id':'zonal_statistics','source_type':'production','source_task_id':'test_ndvi','dataset_id':data['id'],'threshold':.5});prefix='/api/agriculture/tasks/'+task['id']
  for name in ('result.geojson','zones.csv','result.json'):self.assertEqual(self.request(prefix+'/'+name)[0],200)
  self.assertEqual(self.request(prefix+'/analysis.tif')[0],404);self.assertEqual(self.request(prefix+'/overlay.png')[0],404)
  manifest,archive=self.bundle(task)
  with archive:
   self.assertEqual({e['path'] for e in manifest['entries'] if e['role']=='archived_artifact'},{'result.geojson','zones.csv','result.json'})
   readme=archive.read('README.txt').decode('utf-8')
   for text in ('逐地块分区统计','部分覆盖/范围外地块','未覆盖部分不外推','重叠地块','并集阈值命中格网面积'):
    self.assertIn(text,readme)
  status,point=self.request('/api/map/inspect?kind=agriculture&task_id='+task['id']+'&lon=111&lat=30');self.assertEqual(status,422);self.assertEqual(point['error']['code'],'INSPECTION_UNSUPPORTED')
  status,search=self.request('/api/map/search?q=zonal_statistics&remote=0');self.assertEqual(status,200);hit=next(h for h in search['results'] if h.get('task_id')==task['id']);self.assertEqual(hit['result_ref']['image_kind'],'zonal_statistics')
 def test_cancel_and_retry_endpoints_preserve_frozen_source(self):
  gate=threading.Event();original=self.service._read
  def blocked(*args):gate.wait(2);return original(*args)
  with patch.object(self.service,'_read',side_effect=blocked):
   status,task=self.request('/api/agriculture/tasks',self.fvc_request());self.assertEqual(status,201)
   try:
    for _ in range(100):
     if self.service.get(task['id'])['progress']>=15:break
     time.sleep(.01)
    endpoint='/api/agriculture/tasks/'+task['id']
    self.assertEqual(self.request(endpoint+'/cancel',{'extra':True})[0],400)
    status,cancelled=self.request(endpoint+'/cancel',{});self.assertEqual(status,200);self.assertEqual(cancelled['status'],'cancelled')
   finally:gate.set()
  status,retry=self.request(endpoint+'/retry',{});self.assertEqual(status,201,retry);self.assertNotEqual(task['id'],retry['id'])
  result=fixtures.wait(self.service,retry['id']);self.assertEqual(result['status'],'succeeded',result.get('error'));self.assertEqual(result['plan']['source']['artifact_sha256'],task['plan']['source']['artifact_sha256'])


if __name__=='__main__':unittest.main()
