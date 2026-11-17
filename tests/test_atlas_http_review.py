"""Actual local HTTP version and availability checks in an isolated fixture store."""
import json,threading,unittest
from urllib.request import urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode
from backend.server import LocalServer,make_handler
import test_regional_native as fixtures

class AtlasHTTPReviewTest(unittest.TestCase):
 def setUp(self):
  self.fixture=fixtures.RegionalNativeTest();self.fixture.setUp();self.w=self.fixture.w;self.ident=self.fixture.ident;self.server=LocalServer(('127.0.0.1',0),make_handler(self.w));self.server.quiet=True;self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.server.server_port)
 def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join(2);self.fixture.tearDown()
 def req(self,path):
  try:r=urlopen(self.base+path,timeout=10)
  except HTTPError as e:r=e
  with r:return r.status,r.read()
 def test_content_version_guard_applies_to_detail_files_and_zip(self):
  product=self.w.agriculture_atlas.get(self.ident);prefix='/api/agriculture-atlas/'+self.ident
  for suffix in ['', '/files/overlay.png','/bundle.zip']:
   self.assertEqual(self.req(prefix+suffix+'?product_sha256='+product['product_sha256'])[0],200)
   self.assertEqual(self.req(prefix+suffix+'?product_sha256='+'f'*64)[0],409)
  for query in ['product_sha256=','product_sha256=short','product_sha256='+'f'*64+'&product_sha256='+'f'*64,'path=../integrations/tianditu.json']:
   self.assertEqual(self.req(prefix+'?'+query)[0],400)
 def test_list_reports_physical_records_and_native_availability(self):
  code,data=self.req('/api/agriculture-atlas');r=json.loads(data);self.assertEqual(code,200);self.assertEqual(r['production_record_count'],1);self.assertIn('product_sha256',r['products'][0]);self.assertIn('quality',r['products'][0])
  (self.fixture.root/'agriculture-atlas'/self.ident/'analysis.tif').unlink();code,data=self.req('/api/agriculture-atlas');r=json.loads(data);self.assertEqual(r['products'],[]);self.assertIn(self.ident,r['unavailable_product_ids']);self.assertEqual(self.req('/api/agriculture-atlas/'+self.ident)[0],422)
 def test_old_question_download_cannot_serve_new_version_even_same_preview(self):
  p=self.fixture.root/'agriculture-atlas'/self.ident/'result.json';before=self.w.agriculture_atlas.get(self.ident);raw=json.loads(p.read_text());raw['title']='Updated source metadata';p.write_text(json.dumps(raw));prefix='/api/agriculture-atlas/'+self.ident
  for suffix in ['/files/result.json','/files/overlay.png','/bundle.zip']:self.assertEqual(self.req(prefix+suffix+'?product_sha256='+before['product_sha256'])[0],409)
 def test_pixel_get_requires_matching_version_and_typed_query(self):
  p=self.w.agriculture_atlas.get(self.ident);query={'kind':'agriculture_atlas','task_id':self.ident,'lon':113.08,'lat':30.53,'product_sha256':p['product_sha256']};self.assertEqual(self.req('/api/map/inspect?'+urlencode(query))[0],200);query['product_sha256']='f'*64;self.assertEqual(self.req('/api/map/inspect?'+urlencode(query))[0],409);self.assertEqual(self.req('/api/map/inspect?'+urlencode(query)+'&url=https://other.invalid')[0],400)
