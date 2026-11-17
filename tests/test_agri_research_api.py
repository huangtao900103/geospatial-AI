"""Actual local HTTP routes against temporary native rasters and a temporary DB."""
import copy
import hashlib
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np

import test_agri_research_services as service_contract
from backend.server import LocalServer, Workspace, make_handler


class AgriResearchAPITest(unittest.TestCase):
    def setUp(self):
        self.fixture = service_contract.AgriResearchServiceTest('runTest')
        self.fixture.setUp()
        self.workspace = Workspace(self.fixture.store.db_path)
        self.server = LocalServer(('127.0.0.1',0),make_handler(self.workspace))
        self.server.quiet = True
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.prefix = '/api/agri-research'

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)
        self.workspace.close()
        self.fixture.tearDown()

    def request(self, path, body=None, binary=False):
        headers = {'Content-Type':'application/json','Origin':self.base}
        data = json.dumps(body,allow_nan=False).encode() if body is not None else None
        try:
            response = urlopen(Request(self.base+path,data=data,headers=headers),timeout=10)
        except HTTPError as error:
            response = error
        with response:
            payload = response.read()
            return response.status, payload if binary else json.loads(payload)

    def import_body(self):
        return {'name':'isolated HTTP sample contract','series_id':self.fixture.ident,
                'label_source':copy.deepcopy(self.fixture.source),
                'geojson':{'type':'FeatureCollection','features':[self.fixture.point()]}}

    def review_body(self):
        return {'series_id':self.fixture.ident,'change_id':'change-1',
                'threshold':.4,'min_pixels':2,'direction':'both'}

    def test_catalog_series_and_pinned_native_download_roundtrip(self):
        code,catalog = self.request(self.prefix+'/catalog')
        self.assertEqual(code,200)
        self.assertEqual(catalog['classifier']['status'],'needs_samples')
        self.assertIsNone(catalog['classifier'].get('validation'))
        self.assertEqual([row['id'] for row in catalog['series']],[self.fixture.ident])
        series_path = self.prefix+'/series/'+self.fixture.ident
        code,series = self.request(series_path);self.assertEqual(code,200)
        version = series['series_sha256'];self.assertEqual(len(version),64)
        code,raw = self.request(series_path+'/files/observation-1.tif?series_sha256='+version,binary=True)
        self.assertEqual(code,200)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),self.fixture.file_hash('observation-1.tif'))
        code,manifest = self.request(series_path+'/files/result.json?series_sha256='+version)
        self.assertEqual(code,200);self.assertEqual(manifest['id'],self.fixture.ident)
        code,error = self.request(series_path+'?series_sha256='+'0'*64)
        self.assertEqual(code,409);self.assertEqual(error['error']['code'],'SERIES_VERSION_CHANGED')

    def test_http_sample_import_reads_native_pixels_and_training_exposes_a_real_blocker(self):
        code,samples = self.request(self.prefix+'/samples',self.import_body())
        self.assertEqual(code,201)
        self.assertEqual(samples['status'],'needs_samples')
        self.assertAlmostEqual(samples['samples'][0]['features']['ndvi_2026_05_15'],.25)
        self.assertAlmostEqual(samples['samples'][0]['features']['ndmi_2026_05_15'],.35,places=6)
        self.assertNotIn('untrusted_client_ndvi',samples['samples'][0]['features'])
        self.assertEqual(samples['provenance']['label_truth_status'],'provider_declared_not_platform_verified')
        code,model = self.request(self.prefix+'/train',{'sample_set_id':samples['id']})
        self.assertEqual(code,201);self.assertEqual(model['status'],'needs_samples')
        self.assertIsNone(model.get('model'));self.assertIsNone(model.get('validation'))
        self.assertTrue(model['issues'])
        code,error = self.request(self.prefix+'/train',{'sample_set_id':samples['id'],'code':'not accepted'})
        self.assertEqual(code,400);self.assertEqual(error['error']['code'],'INVALID_TRAIN_INPUT')
        (self.fixture.folder/'publication.json').write_text('{"revision":"isolated HTTP update"}')
        code,error = self.request(self.prefix+'/train',{'sample_set_id':samples['id']})
        self.assertEqual(code,409);self.assertEqual(error['error']['code'],'SERIES_VERSION_CHANGED')

    def test_http_review_creation_geojson_and_status_append_keep_the_native_result_frozen(self):
        before_hash = self.fixture.file_hash('change.tif')
        code,review = self.request(self.prefix+'/reviews',self.review_body())
        self.assertEqual(code,201);self.assertAlmostEqual(review['summary']['area_sum_ha'],.36,places=6)
        ident = review['id'];candidate = review['geojson']['features'][0]['id']
        frozen = copy.deepcopy(review['geojson'])
        review_path = self.prefix+'/reviews/'+ident
        code,detail = self.request(review_path);self.assertEqual(code,200)
        self.assertEqual(detail['geojson']['features'][0]['properties']['review_status'],'pending')
        code,status = self.request(review_path+'/status',{'candidate_id':candidate,'status':'verified','note':'isolated manual test state'})
        self.assertEqual(code,200);self.assertEqual(len(status['history']),1)
        code,geojson = self.request(review_path+'/geojson')
        self.assertEqual(code,200);self.assertEqual(geojson['type'],'FeatureCollection')
        self.assertEqual(geojson['features'][0]['properties']['review_status'],'verified')
        self.assertEqual(self.workspace.get('agri_review',ident)['geojson'],frozen)
        self.assertEqual(self.fixture.file_hash('change.tif'),before_hash)
        self.assertEqual(self.workspace.list('crop_sample_set'),[])

    def test_no_common_support_returns_422_without_an_empty_success_record(self):
        fixture = self.fixture
        fixture.write_raster('change.tif',[np.zeros(fixture.shape),np.zeros(fixture.shape),np.ones(fixture.shape)],
                             ('value','valid_mask','city_mask'))
        fixture.raw['changes'][0]['native_sha256']=fixture.file_hash('change.tif');fixture.write_result()
        code,error = self.request(self.prefix+'/reviews',self.review_body())
        self.assertEqual(code,422);self.assertEqual(error['error']['code'],'CHANGE_NO_COMMON_SUPPORT')
        self.assertEqual(self.workspace.list('agri_review'),[])

    def test_query_and_provenance_contract_failures_return_structured_client_errors(self):
        path = self.prefix+'/series/'+self.fixture.ident
        for query in ('?unknown=1','?series_sha256=bad','?series_sha256='+'a'*64+'&series_sha256='+'b'*64):
            code,error = self.request(path+query)
            self.assertEqual(code,400);self.assertEqual(error['error']['code'],'INVALID_SERIES_QUERY')
        code,error = self.request(self.prefix+'/catalog?series_sha256='+'a'*64)
        self.assertEqual(code,400)
        code,error = self.request(path+'/files/not-declared.tif')
        self.assertEqual(code,404);self.assertEqual(error['error']['code'],'SERIES_FILE_NOT_FOUND')
        body = self.import_body();body['label_source']['method']='model_prediction'
        code,error = self.request(self.prefix+'/samples',body)
        self.assertEqual(code,422);self.assertEqual(error['error']['code'],'SAMPLE_SOURCE_REQUIRED')
        body = self.import_body();body['geojson']['features'][0]['geometry']=['invalid object']
        code,error = self.request(self.prefix+'/samples',body)
        self.assertEqual(code,422);self.assertEqual(error['error']['code'],'INVALID_SAMPLE_GEOMETRY')
        self.assertEqual(self.workspace.list('crop_sample_set'),[])


if __name__=='__main__':unittest.main()
