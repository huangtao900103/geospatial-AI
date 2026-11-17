"""Reproducible exports bind actual source content, not just editable metadata."""
import hashlib,json,tempfile,unittest,zipfile
from pathlib import Path
from backend.server import Workspace
from backend.core.geo_services import digest,GeoError
class SceneBundleTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.ws=Workspace(Path(self.temp.name)/'test.sqlite3')
  self.d=self.ws.create_dataset({'name':'隔离复核输入','geojson':{'type':'FeatureCollection','features':[{'type':'Feature','id':str(i),'properties':{'状态':state},'geometry':{'type':'Point','coordinates':[113.4+i*.01,30.3]}} for i,state in enumerate(['在建','已完成'])]}})
  self.r=self.ws.scene_workflows.run('tailwater',{'dataset_id':self.d['id'],'query':'在建','group_by':'状态'})
 def tearDown(self):self.ws.close();self.temp.cleanup()
 def test_full_source_filter_and_human_limitations_are_reproducible(self):
  path=self.ws.result_bundles.artifact('analysis',self.r['id'])
  with zipfile.ZipFile(path) as z:
   source=json.loads(z.read('source.geojson'));result=json.loads(z.read('result.geojson'));manifest=json.loads(z.read('manifest.json'))
   self.assertEqual(len(source['features']),2);self.assertEqual(len(result['features']),1);self.assertEqual(digest(source),self.r['provenance']['sha256'])
   for e in manifest['entries']:self.assertEqual(hashlib.sha256(z.read(e['path'])).hexdigest(),e['sha256'])
   readme=z.read('README.txt').decode();self.assertIn('规范化内容哈希',readme);self.assertIn('未做跨要素去重',readme);self.assertIn('不推算作业面积',readme)
 def test_actual_source_tamper_rejected_even_if_metadata_hash_unchanged(self):
  self.ws.result_bundles.artifact('analysis',self.r['id']) # Also checks cached exports cannot bypass source check.
  changed=self.ws.get_dataset(self.d['id']);changed['geojson']['features'][0]['geometry']['coordinates'][0]+=1;self.ws.save('dataset',changed)
  with self.assertRaises(GeoError) as error:self.ws.result_bundles.artifact('analysis',self.r['id'])
  self.assertEqual(error.exception.code,'BUNDLE_SOURCE_CHANGED')
 def test_source_version_change_rejected(self):
  changed=self.ws.get_dataset(self.d['id']);changed['version']=2;self.ws.save('dataset',changed)
  with self.assertRaises(GeoError):self.ws.result_bundles.artifact('analysis',self.r['id'])
