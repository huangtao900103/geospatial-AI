"""Protocol/security tests with synthetic responses; not live-key verification."""
import base64
import copy
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from http.client import HTTPException
from urllib.parse import parse_qs, urlsplit

from backend.core.geo_services import GeoError
from backend.core.search_services import SearchServices
from backend.integrations.tianditu_services import TiandituServices, boundary_geometry, NoRedirect

TOKEN = 'a1' * 16
OTHER_TOKEN = 'b2' * 16
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aQ1cAAAAASUVORK5CYII=')
WKT = 'MULTIPOLYGON(((113 30,114 30,114 31,113 31,113 30),(113.2 30.2,113.3 30.2,113.3 30.3,113.2 30.2)),((114.1 30,114.2 30,114.2 30.1,114.1 30)))'
ADMIN = {'status': 200, 'message': '成功', 'data': {'suggestion': [], 'district': [{'name': '单元测试参考区', 'gb': '156429004', 'boundary': WKT, 'center': {'lng': 113.5, 'lat': 30.5}, 'level': 2}]}}
SEARCH = {'status': {'infocode': 1000, 'cndesc': '成功'}, 'resultType': 1, 'pois': [{'name': '测试地点', 'lonlat': '114.3,30.6', 'address': '测试地址', 'hotPointID': 'fixture-point'}]}
REVERSE = {'status': '0', 'msg': 'OK', 'result': {'formatted_address': '单元测试地址', 'addressComponent': {'city': '测试市', 'road': '测试路'}, 'location': {'lon': 114.3, 'lat': 30.6}}}

class Response:
    def __init__(self, value, content_type='application/json'):
        self.data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.headers = {'Content-Type': content_type}
    def read(self, count): return self.data[:count]
    def __enter__(self): return self
    def __exit__(self, *args): pass

class Opener:
    def __init__(self): self.calls = []; self.override = {}
    def open(self, request, timeout):
        self.calls.append((request.full_url, timeout)); path = urlsplit(request.full_url).path
        value = self.override.get(path)
        if callable(value): return value(request)
        if isinstance(value, Exception): raise value
        if value is not None: return Response(value)
        if path == '/v2/search': return Response(copy.deepcopy(SEARCH))
        if path == '/geocoder': return Response(copy.deepcopy(REVERSE))
        if path == '/v2/administrative': return Response(copy.deepcopy(ADMIN))
        if path.endswith('_w/wmts'): return Response(PNG, 'image/png')
        raise AssertionError('Unexpected endpoint')

class Store:
    def __init__(self): self.rows = {}
    def list(self, kind): return copy.deepcopy(self.rows.get(kind, []))

class TiandituTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=False); self.env.start()
        self.old_env = os.environ.pop('TIANDITU_TOKEN', None)
        self.temp = tempfile.TemporaryDirectory(); self.path = Path(self.temp.name) / 'integrations/tianditu.json'; self.opener = Opener()
        self.service = TiandituServices(self.path, opener=self.opener)
    def tearDown(self):
        self.env.stop(); self.temp.cleanup()
    def configured(self): self.service.configure({'token': TOKEN}); return self.service
    def test_unconfigured_is_truthful_and_does_not_create_config_or_call_network(self):
        status = self.service.status(); self.assertFalse(status['configured']); self.assertEqual(status['status'], 'not_configured')
        self.assertFalse(self.path.exists()); self.assertFalse(self.opener.calls)
        for call in [lambda:self.service.search('武汉'), lambda:self.service.reverse(114,30), lambda:self.service.administrative('仙桃市'), lambda:self.service.tile('img',10,837,420), self.service.test]:
            with self.subTest(call=call), self.assertRaises(GeoError) as caught: call()
            self.assertEqual(caught.exception.code,'TIANDITU_NOT_CONFIGURED')
        self.service.clear(); self.assertFalse(self.path.exists()); self.assertFalse(self.opener.calls)
    def test_private_file_roundtrip_and_no_key_in_status(self):
        status = self.configured().status(); self.assertTrue(status['configured']); self.assertEqual(status['status'],'configured')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode),0o600)
        self.assertEqual(json.loads(self.path.read_text())['token'],TOKEN)
        reloaded = TiandituServices(self.path, opener=self.opener); self.assertTrue(reloaded.status()['configured']); self.assertEqual(reloaded.status()['key_source'],'local_file')
        self.assertNotIn(TOKEN,json.dumps(status)); self.assertFalse(self.opener.calls)
        self.service.clear(); self.assertFalse(self.path.exists()); self.assertFalse(self.service.status()['configured'])
    def test_invalid_credentials_and_unknown_config_fields_rejected(self):
        for body in ({'token':'x'*32},{'token':123},{'token':TOKEN,'url':'https://evil.test'},{'other':TOKEN},None):
            with self.subTest(body=body),self.assertRaises(GeoError):self.service.configure(body)
        self.assertFalse(self.path.exists())
    def test_environment_precedence_clear_and_invalid_environment(self):
        self.configured()
        with patch.dict(os.environ,{'TIANDITU_TOKEN':OTHER_TOKEN}):
            self.assertEqual(self.service.status()['key_source'],'environment')
            with self.assertRaises(GeoError) as caught:self.service.configure({'token':TOKEN})
            self.assertEqual(caught.exception.code,'TIANDITU_ENV_MANAGED')
            cleared=self.service.clear(); self.assertTrue(cleared['configured']); self.assertIn('环境变量',cleared['message']); self.assertFalse(self.path.exists())
        with patch.dict(os.environ,{'TIANDITU_TOKEN':'bad'}):
            self.assertEqual(self.service.status()['status'],'restricted')
            with self.assertRaises(GeoError) as caught:self.service.search('武汉')
            self.assertEqual(caught.exception.code,'INVALID_TIANDITU_ENV')
    def test_corrupt_config_is_not_reported_connected(self):
        self.path.parent.mkdir();self.path.write_text('{invalid')
        service=TiandituServices(self.path,opener=self.opener);status=service.status();self.assertFalse(status['configured']);self.assertIn('不可读取',status['message'])
    def test_search_uses_official_protocol_and_only_navigation_points(self):
        hit=self.configured().search('武汉大学',3)[0]
        url,timeout=self.opener.calls[0]; parts=urlsplit(url);p=parse_qs(parts.query);post=json.loads(p['postStr'][0])
        self.assertEqual(parts.hostname,'api.tianditu.gov.cn');self.assertEqual(parts.path,'/v2/search');self.assertEqual(p['type'],['query']);self.assertEqual(p['tk'],[TOKEN])
        self.assertEqual(post['queryType'],1);self.assertEqual(post['keyWord'],'武汉大学');self.assertEqual(post['count'],3)
        self.assertEqual(hit['center'],[114.3,30.6]);self.assertFalse(hit['aoi_eligible']);self.assertNotIn('bbox',hit);self.assertNotIn(TOKEN,json.dumps(hit))
    def test_administrative_search_point_is_not_rectangular_analysis_mask(self):
        self.configured();self.opener.override['/v2/search']={'status':{'infocode':1000},'resultType':3,'area':{'name':'仙桃市','lonlat':'113.4,30.3','bound':'113,30,114,31','adminCode':'156429004'}}
        hit=self.service.search('仙桃市')[0];self.assertEqual(hit['center'],[113.4,30.3]);self.assertNotIn('bbox',hit);self.assertFalse(hit['aoi_eligible'])
    def test_bad_remote_points_are_filtered_and_no_data_is_empty(self):
        self.configured();self.opener.override['/v2/search']={'status':{'infocode':1000},'pois':[{'name':'bad','lonlat':'nan,30'},{'name':'bad','lonlat':'181,30'},None]}
        self.assertEqual(self.service.search('武汉'),[])
        self.opener.override['/v2/search']={'status':{'infocode':3001}};self.assertEqual(self.service.search('没有数据'),[])
    def test_upstream_errors_are_sanitized_and_do_not_echo_keys_or_provider_text(self):
        self.configured()
        for error in (URLError('https://example.test/?tk='+TOKEN),HTTPException('https://example.test/?tk='+TOKEN)):
            self.opener.override['/v2/search']=error
            with self.assertRaises(GeoError) as caught:self.service.search('武汉')
            self.assertNotIn(TOKEN,caught.exception.message);self.assertNotIn('example.test',caught.exception.message)
        self.opener.override['/v2/search']={'status':{'infocode':9999,'cndesc':'private '+TOKEN}}
        with self.assertRaises(GeoError) as caught:self.service.search('武汉')
        self.assertNotIn(TOKEN,caught.exception.message)
    def test_reverse_protocol_and_sanitized_components(self):
        r=self.configured().reverse('114.3','30.6');self.assertEqual(r['address'],'单元测试地址');self.assertFalse(r['aoi_eligible'])
        params=parse_qs(urlsplit(self.opener.calls[0][0]).query);self.assertEqual(params['type'],['geocode']);self.assertEqual(json.loads(params['postStr'][0]),{'lon':114.3,'lat':30.6,'ver':1})
        self.assertEqual(r['components'],{'city':'测试市','road':'测试路'});self.assertNotIn(TOKEN,json.dumps(r))
    def test_invalid_inputs_fail_before_network(self):
        self.configured()
        for call in [lambda:self.service.search('x\ny'),lambda:self.service.search('x'*201),lambda:self.service.search('武汉',True),lambda:self.service.reverse('nan',30),lambda:self.service.reverse(True,30),lambda:self.service.administrative(''),lambda:self.service.tile('evil',10,0,0),lambda:self.service.tile('img',0,0,0),lambda:self.service.tile('img',19,0,0),lambda:self.service.tile('img',2,4,1)]:
            with self.subTest(call=call),self.assertRaises(GeoError):call()
        self.assertFalse(self.opener.calls)
    def test_wmts_protocol_and_no_persistence_or_credentialed_metadata(self):
        r=self.configured().tile('cia','10','837','420');self.assertEqual(r['data'],PNG);self.assertEqual(r['content_type'],'image/png');self.assertEqual(r['cache_control'],'private, no-store')
        parts=urlsplit(self.opener.calls[0][0]);params=parse_qs(parts.query)
        self.assertEqual(parts.hostname,'t0.tianditu.gov.cn');self.assertEqual(parts.path,'/cia_w/wmts');self.assertEqual(params['LAYER'],['cia']);self.assertEqual(params['TILEMATRIXSET'],['w']);self.assertEqual(params['TILECOL'],['837']);self.assertEqual(params['TILEROW'],['420']);self.assertNotIn('tk=',r['source_url'])
        self.assertEqual(list(self.path.parent.iterdir()),[self.path])
    def test_tile_error_document_is_not_presented_as_imagery(self):
        self.configured();self.opener.override['/img_w/wmts']={'error':'authorization '+TOKEN}
        with self.assertRaises(GeoError) as caught:self.service.tile('img',10,837,420)
        self.assertEqual(caught.exception.code,'TIANDITU_INVALID_TILE');self.assertNotIn(TOKEN,caught.exception.message)
    def test_administrative_v2_contract_holes_multipolygons_unknown_version(self):
        r=self.configured().administrative('仙桃市');item=r['results'][0]
        parts=urlsplit(self.opener.calls[0][0]);params=parse_qs(parts.query)
        self.assertEqual(parts.path,'/v2/administrative');self.assertEqual(params['childLevel'],['0']);self.assertEqual(params['extensions'],['true']);self.assertEqual(params['keyword'],['仙桃市'])
        self.assertEqual(item['geojson']['type'],'FeatureCollection');g=item['geojson']['features'][0]['geometry'];self.assertEqual(g['type'],'MultiPolygon');self.assertEqual(len(g['coordinates']),2);self.assertEqual(len(g['coordinates'][0]),2);self.assertEqual(item['bbox'],[113,30,114.2,31]);self.assertIsNone(item['data_version']);self.assertEqual(item['boundary_status'],'available')
        self.assertNotIn(TOKEN,json.dumps(r));self.assertEqual(r['suggestions'],[])
    def test_ambiguous_candidates_stay_separate_and_bad_boundary_is_not_bbox(self):
        self.configured();doc=copy.deepcopy(ADMIN);second=copy.deepcopy(doc['data']['district'][0]);second['name']='另一个区';second['gb']='156429005';second['boundary']='MULTIPOLYGON(((113 30,114 31,...)))';doc['data']['district'].append(second);doc['data']['suggestion']=['单元测试参考区',{'name':'另一个区'}];self.opener.override['/v2/administrative']=doc
        r=self.service.administrative('测试区');self.assertEqual(len(r['results']),2);bad=r['results'][1];self.assertIsNone(bad['geojson']);self.assertNotIn('bbox',bad);self.assertEqual(bad['boundary_status'],'unavailable');self.assertEqual(r['suggestions'],['单元测试参考区','另一个区']);self.assertTrue(r['warnings'])
    def test_check_verifies_all_seven_real_request_shapes_and_has_no_key(self):
        s=self.configured().test();self.assertEqual(s['status'],'verified');self.assertEqual(len(self.opener.calls),7)
        self.assertEqual(set(s['services']['tiles']['layers']),{'img','cia','vec','cva'});self.assertEqual(s['services']['administrative']['status'],'verified');self.assertTrue(s['checked_at']);self.assertNotIn(TOKEN,json.dumps(s))
    def test_check_reports_partial_when_one_layer_fails(self):
        self.configured();self.opener.override['/cva_w/wmts']=URLError('failed')
        s=self.service.test();self.assertEqual(s['status'],'partial');self.assertEqual(s['services']['tiles']['status'],'partial');self.assertEqual(s['services']['tiles']['layers']['cva']['status'],'failed')
    def test_configuration_generation_discards_old_check_even_if_key_changes_back(self):
        self.configured();started=threading.Event();release=threading.Event();result=[]
        def slow(req):started.set();release.wait(2);return Response(PNG,'image/png')
        self.opener.override['/img_w/wmts']=slow
        thread=threading.Thread(target=lambda:result.append(self.service.test()));thread.start();self.assertTrue(started.wait(1))
        self.service.configure({'token':OTHER_TOKEN});self.service.configure({'token':TOKEN});release.set();thread.join(3);self.assertFalse(thread.is_alive());self.assertEqual(self.service.status()['status'],'configured');self.assertIsNone(self.service.status()['checked_at']);self.assertIn('未应用',result[0]['message'])
    def test_queue_is_bounded_and_no_request_sent_when_full(self):
        self.configured()
        for _ in range(28):self.assertTrue(self.service.admission.acquire(blocking=False))
        try:
            with self.assertRaises(GeoError) as caught:self.service.search('武汉')
            self.assertEqual(caught.exception.code,'TIANDITU_BUSY');self.assertFalse(self.opener.calls)
        finally:
            for _ in range(28):self.service.admission.release()
    def test_redirect_never_forwards_key(self):
        with self.assertRaises(GeoError):NoRedirect().redirect_request(None,None,302,'Moved',{},'https://evil.test')

class WktTest(unittest.TestCase):
    def test_polygon_ring_and_exponents(self):
        g,b,n=boundary_geometry(' polygon ((1.13e2 30,114 30,114 31,113 30)) ');self.assertEqual(g['type'],'Polygon');self.assertEqual(b,[113,30,114,31]);self.assertEqual(n,4)
    def test_reject_invalid_structure_nonfinite_open_ring_and_trailing_garbage(self):
        invalid=['','POINT(113 30)','POLYGON Z((113 30 1,114 30 1,114 31 1,113 30 1))','POLYGON((113 30,114 30,114 31))','POLYGON((113 30,113 30,113 30,113 30))','POLYGON((113 30,181 30,114 31,113 30))','POLYGON((113 30,114 NaN,114 31,113 30))','POLYGON((113 30,114 1e999,114 31,113 30))','POLYGON((113 30,114 30,114 31,113 30)) junk','MULTIPOLYGON((((113 30,114 30,114 31,113 30))))','MULTIPOLYGON EMPTY','POLYGON((113.130,114 30,114 31,113 30))','POLYGON((113 30,114 30,114 31,113 30);DROP TABLE x)']
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(GeoError):boundary_geometry(value)
    def test_vertex_limit(self):
        with self.assertRaises(GeoError) as caught:boundary_geometry(WKT,point_limit=5)
        self.assertEqual(caught.exception.code,'TIANDITU_BOUNDARY_TOO_COMPLEX')

class SearchIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.opener=Opener();self.env=patch.dict(os.environ,{},clear=False);self.env.start();os.environ.pop('TIANDITU_TOKEN',None)
        self.tdt=TiandituServices(Path(self.temp.name)/'tdt.json',opener=self.opener);self.tdt.configure({'token':TOKEN});self.store=Store();self.search=SearchServices(self.store,tianditu=self.tdt)
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def test_autocomplete_and_coordinates_never_consume_remote_quota(self):
        self.assertTrue(self.search.search('武汉',remote=False)['results']);self.assertEqual(self.search.search('114.3,30.6')['results'][0]['kind'],'coordinate');self.assertFalse(self.opener.calls)
    def test_explicit_search_merges_remote_and_preserves_local_reference(self):
        r=self.search.search('武汉',remote=True);self.assertEqual(r['results'][0]['id'],'tianditu:fixture-point');self.assertEqual(r['remote_status'],'completed');self.assertTrue(any(h['source_type']=='public_reference' for h in r['results']));self.assertEqual(len(self.opener.calls),1)
    def test_remote_failure_preserves_local_hits_and_clear_warning(self):
        self.opener.override['/v2/search']=URLError('private '+TOKEN);r=self.search.search('武汉',remote=True);self.assertTrue(r['results']);self.assertEqual(r['remote_status'],'failed');self.assertEqual(r['warnings'][0]['code'],'TIANDITU_UNAVAILABLE');self.assertNotIn(TOKEN,json.dumps(r))
    def test_exact_workspace_result_avoids_remote_delay(self):
        self.store.rows['dataset']=[{'id':'data-1','name':'我的巡查台账','source_type':'user','bbox':[113,30,114,31],'feature_count':1}]
        r=self.search.search('我的巡查台账',remote=True);self.assertEqual(r['results'][0]['dataset_id'],'data-1');self.assertEqual(r['remote_status'],'workspace_exact_match');self.assertFalse(self.opener.calls)

if __name__=='__main__':unittest.main()
