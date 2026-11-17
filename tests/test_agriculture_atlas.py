"""Regional atlas serves only produced files; isolated records are never business seed."""
import hashlib,json,tempfile,unittest,zipfile
from pathlib import Path
from backend.agriculture.agriculture_atlas import AgricultureAtlas
from backend.core.geo_services import GeoError
class AgricultureAtlasTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.atlas=AgricultureAtlas(self.root)
 def tearDown(self):self.temp.cleanup()
 def product(self):
  p=self.root/'xiantao-test';p.mkdir();data=b'verified raster artifact';(p/'overlay.png').write_bytes(data)
  raw={'schema_version':'agriculture-atlas-1.0','id':p.name,'title':'Isolated calculation record','status':'ready','scene_ids':['farmland'],'scope_label':'test','period_label':'2024','summary_metrics':[{'label':'unknown','value':None,'unit':'ha'},{'label':'zero','value':0,'unit':'ha'}],'layers':[{'id':'view','kind':'raster','image':{'url':'overlay.png','bounds':[[30,113],[31,114]]},'legend':[]}],'provenance':{},'artifacts':[{'file':'overlay.png','sha256':hashlib.sha256(data).hexdigest()}]}
  (p/'result.json').write_text(json.dumps(raw));return p,raw
 def test_empty_atlas_never_seeds_examples(self):self.assertEqual(self.atlas.list()['products'],[]);self.assertEqual(list(self.root.iterdir()),[])
 def test_normalized_real_files_and_zero_unknown_preserved(self):
  p,r=self.product();v=self.atlas.get(p.name);self.assertEqual(v['summary_metrics'][0]['value'],None);self.assertEqual(v['summary_metrics'][1]['value'],0);self.assertEqual(v['layers'][0]['image']['bounds'],[[30,113],[31,114]]);self.assertTrue(v['layers'][0]['image']['url'].endswith('/files/overlay.png'))
 def test_non_spatial_publication_has_no_invented_map(self):
  p,r=self.product();r['layers']=[];r['artifacts']=[];(p/'result.json').write_text(json.dumps(r));self.assertEqual(self.atlas.get(p.name)['layers'],[])
 def test_unknown_invalid_and_traversal_ids_rejected(self):
  for id in ['../data','x/../../config',[],None]:
   with self.assertRaises(GeoError):self.atlas.get(id)
  with self.assertRaises(GeoError):self.atlas.get('not-created')
 def test_unlisted_and_symlink_files_cannot_escape(self):
  p,r=self.product();(p/'secret.json').write_text('not public')
  with self.assertRaises(GeoError):self.atlas.artifact(p.name,'secret.json')
  with self.assertRaises(GeoError):self.atlas.artifact(p.name,'../secret')
  (p/'overlay.png').unlink();(p/'overlay.png').symlink_to(p/'secret.json')
  with self.assertRaises(GeoError):self.atlas.get(p.name)
 def test_mutated_artifact_fails_actual_content_hash(self):
  p,r=self.product();self.atlas.get(p.name);(p/'overlay.png').write_bytes(b'changed')
  with self.assertRaises(GeoError) as e:self.atlas.get(p.name)
  self.assertEqual(e.exception.code,'ATLAS_SOURCE_CHANGED')
 def test_bundle_all_entries_verified_and_reusable(self):
  p,r=self.product();path=self.atlas.bundle(p.name)
  with zipfile.ZipFile(path) as z:
   self.assertIsNone(z.testzip());manifest=json.loads(z.read('manifest.json'));self.assertIn('product.json',z.namelist())
   for item in manifest['entries']:self.assertEqual(hashlib.sha256(z.read(item['path'])).hexdigest(),item['sha256'])
  self.assertEqual(self.atlas.bundle(p.name),path)
 def test_corrupt_product_does_not_hide_healthy_products(self):
  p,r=self.product();bad=self.root/'xiantao-broken';bad.mkdir();(bad/'result.json').write_text('{bad');self.assertEqual(len(self.atlas.list()['products']),1)
 def test_partial_regeneration_cannot_serve_stale_result(self):
  p,r=self.product();(p/'status.json').write_text(json.dumps({'status':'running'}));self.assertEqual(self.atlas.list()['products'],[])
  with self.assertRaises(GeoError):self.atlas.get(p.name)
 def test_version_binds_declared_artifact_bytes_even_when_summary_unchanged(self):
  p,r=self.product();before=self.atlas.get(p.name);data=b'new correct source content';(p/'overlay.png').write_bytes(data);r['artifacts'][0]['sha256']=hashlib.sha256(data).hexdigest();(p/'result.json').write_text(json.dumps(r));after=self.atlas.get(p.name);self.assertNotEqual(before['product_sha256'],after['product_sha256']);self.assertNotEqual(before['artifact_sha256'],after['artifact_sha256'])
  with self.assertRaises(GeoError):self.atlas.require_version(p.name,before['product_sha256'])
 def test_non_object_metadata_is_not_a_product_and_does_not_break_list(self):
  p,r=self.product();bad=self.root/'xiantao-invalid';bad.mkdir();(bad/'result.json').write_text('[]');self.assertEqual(len(self.atlas.list()['products']),1)
