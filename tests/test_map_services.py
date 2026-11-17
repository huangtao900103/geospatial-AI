"""Bounded tile proxy, source isolation and public map snapshot contracts."""
import copy
import hashlib
import io
import json
from pathlib import Path
from email.utils import formatdate
from concurrent.futures import ThreadPoolExecutor
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core.geo_services import GeoError, PublicSources
from backend.core.map_services import (MapServices, TILE_LIMIT, TILE_TEMPLATES, SOURCE_DIR, CACHE_MAX_FILES,
                          CACHE_MAX_BYTES, response_cache_ttl, tile_coordinates, image_type,
                          TileRedirect, clip_segment)
from backend.server import FIXTURE, Workspace, LocalServer, make_handler

# Minimal deterministic marker fixture; production upstream is tested separately.
JPEG=b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9'
PNG=b'\x89PNG\r\n\x1a\n'+b'0'*20+b'IEND'+b'0'*4

class Response:
    def __init__(self,data=JPEG,headers=None):
        self.data=data
        self.headers=headers or {'Content-Type':'image/jpeg','Cache-Control':'public, max-age=120'}
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,n):return self.data[:n]

class TileTest(unittest.TestCase):
    def setUp(self):
        self.maps=MapServices(FIXTURE,PublicSources())
        self.maps.opener.open=Mock(side_effect=lambda *args,**kwargs:Response())

    def test_fixed_sources_zxy_and_no_arbitrary_url(self):
        self.assertEqual(tile_coordinates('imagery','12','3338','1685'),(12,3338,1685))
        for params in [('evil',0,0,0),('https://evil',0,0,0),('imagery',19,0,0),('imagery',1,2,0),('imagery',1,0,2),('imagery',-1,0,0),('imagery',True,0,0),('imagery',1,float('nan'),0),('imagery',1,10**1000,0),('nasa',10,0,0),('imagery','../../',0,0)]:
            with self.subTest(params=params),self.assertRaises(GeoError):tile_coordinates(*params)
        self.maps.tile('imagery',12,3338,1685)
        request=self.maps.opener.open.call_args[0][0]
        self.assertEqual(request.full_url,TILE_TEMPLATES['imagery'].format(z=12,x=3338,y=1685))
        self.assertEqual(self.maps.opener.open.call_args[1]['timeout'],8)
        with self.assertRaises(GeoError):TileRedirect().redirect_request(Request(request.full_url),None,302,'Found',{},'https://evil.example/')

    def test_memory_cache_hit_expiry_and_no_store(self):
        first=self.maps.tile('imagery',12,3338,1685)
        second=self.maps.tile('imagery',12,3338,1685)
        self.assertFalse(first['cached']);self.assertTrue(second['cached'])
        self.assertEqual(first['fetched_at'],second['fetched_at'])
        self.assertEqual(self.maps.opener.open.call_count,1)
        self.maps.cache[('imagery',12,3338,1685)]['expires']=time.monotonic()-1
        self.maps.tile('imagery',12,3338,1685)
        self.assertEqual(self.maps.opener.open.call_count,2)
        maps=MapServices(FIXTURE,PublicSources())
        maps.opener.open=Mock(side_effect=lambda *a,**kw:Response(headers={'Content-Type':'image/jpeg','Cache-Control':'no-store'}))
        maps.tile('imagery',0,0,0);maps.tile('imagery',0,0,0)
        self.assertEqual(maps.opener.open.call_count,2)
        self.assertEqual(len(maps.cache),0)

    def test_cache_headers_and_upstream_age(self):
        self.assertEqual(response_cache_ttl({'Cache-Control':'public,max-age=86400'}),300)
        self.assertEqual(response_cache_ttl({'Cache-Control':'public,max-age=120','Age':'20'}),100)
        self.assertEqual(response_cache_ttl({'Cache-Control':'private,max-age=120'}),0)
        self.assertEqual(response_cache_ttl({'Cache-Control':'no-cache'}),0)
        self.assertEqual(response_cache_ttl({'Cache-Control':'no-store'}),0)

    def test_upstream_freshness_is_reduced_before_local_cache_cap(self):
        self.assertEqual(response_cache_ttl({'Cache-Control':'public,max-age=86400','Age':'3600'}),300)
        self.assertEqual(response_cache_ttl({'Cache-Control':'public,max-age=120','Age':'20'}),100)
        for age in ('120','3600'):
            self.assertEqual(response_cache_ttl({'Cache-Control':'public,max-age=120','Age':age}),0)
        with patch('backend.core.map_services.time.time',return_value=1000000000):
            self.assertEqual(response_cache_ttl({'Expires':formatdate(1000000120,usegmt=True),'Age':'20'}),120)

    def test_invalid_images_and_unavailability_never_fake_tiles(self):
        for data,kind in [(b'{"error":403}','image/jpeg'),(JPEG,'application/json'),(b'x'*(TILE_LIMIT+1),'image/jpeg'),(b'','image/jpeg')]:
            self.maps.opener.open=Mock(return_value=Response(data,{'Content-Type':kind}))
            with self.assertRaises(GeoError) as error:self.maps.tile('imagery',0,0,0)
            self.assertEqual(error.exception.status,503)
            self.assertEqual(len(self.maps.cache),0)
        self.maps.opener.open=Mock(side_effect=URLError('offline'))
        with self.assertRaises(GeoError) as error:self.maps.tile('imagery',0,0,0)
        self.assertEqual(error.exception.code,'TILE_UNAVAILABLE')
        self.assertEqual(image_type(PNG,'image/png'),'image/png')

    def test_lru_limits_and_no_disk_mirroring(self):
        with tempfile.TemporaryDirectory() as directory:
            maps=MapServices(FIXTURE,PublicSources(),cache_dir=directory)
            maps.opener.open=Mock(side_effect=lambda *a,**kw:Response())
            for x in range(CACHE_MAX_FILES+5):maps.tile('imagery',7,x,1)
            self.assertEqual(len(maps.cache),CACHE_MAX_FILES)
            self.assertLessEqual(sum(len(x['result']['data']) for x in maps.cache.values()),CACHE_MAX_BYTES)
            self.assertNotIn(('imagery',7,0,1),maps.cache)
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_viewport_burst_24_requests_complete_in_batches_of_six(self):
        maps=MapServices(FIXTURE,PublicSources(),queue_timeout=1)
        barrier=threading.Barrier(24)
        lock=threading.Lock()
        active=0
        peak=0
        def fetch(*args,**kwargs):
            nonlocal active,peak
            with lock:
                active+=1
                peak=max(peak,active)
            try:
                time.sleep(.015)
                return Response()
            finally:
                with lock: active-=1
        maps.opener.open=Mock(side_effect=fetch)
        def request(x):
            barrier.wait(timeout=2)
            return maps.tile('imagery',7,x,1)
        with ThreadPoolExecutor(max_workers=24) as executor:
            results=list(executor.map(request,range(24)))
        self.assertEqual(len(results),24)
        self.assertTrue(all(result['content_type']=='image/jpeg' for result in results))
        self.assertEqual(maps.opener.open.call_count,24)
        self.assertEqual(peak,6)
        self.assertEqual(maps.config()['capabilities']['tile_max_waiting'],48)

    def test_queue_timeout_releases_capacity_without_marking_upstream_failed(self):
        maps=MapServices(FIXTURE,PublicSources(),network_concurrency=1,max_waiting=1,queue_timeout=.02)
        entered,release=threading.Event(),threading.Event()
        def fetch(*args,**kwargs):
            entered.set()
            release.wait(timeout=1)
            return Response()
        maps.opener.open=Mock(side_effect=fetch)
        with ThreadPoolExecutor(max_workers=1) as executor:
            first=executor.submit(maps.tile,'imagery',3,0,0)
            self.assertTrue(entered.wait(timeout=1))
            try:
                with self.assertRaises(GeoError) as error: maps.tile('imagery',3,1,0)
                self.assertEqual(error.exception.code,'TILE_QUEUE_TIMEOUT')
                self.assertEqual(error.exception.status,503)
                self.assertNotIn('imagery',maps.source_states)
            finally: release.set()
            self.assertEqual(first.result(timeout=1)['content_type'],'image/jpeg')
        self.assertEqual(maps.tile('imagery',3,2,0)['content_type'],'image/jpeg')

    def test_queue_overload_is_bounded_and_admitted_request_can_finish(self):
        maps=MapServices(FIXTURE,PublicSources(),network_concurrency=1,max_waiting=1,queue_timeout=1)
        entered,release,waiting=threading.Event(),threading.Event(),threading.Event()
        semaphore=maps.network_slots
        class SignalWaiting:
            def acquire(self,**kwargs):
                if semaphore.acquire(blocking=False): return True
                waiting.set()
                return semaphore.acquire(**kwargs)
            def release(self): semaphore.release()
        maps.network_slots=SignalWaiting()
        def fetch(*args,**kwargs):
            entered.set()
            release.wait(timeout=1)
            return Response()
        maps.opener.open=Mock(side_effect=fetch)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first=executor.submit(maps.tile,'imagery',3,0,0)
            self.assertTrue(entered.wait(timeout=1))
            second=executor.submit(maps.tile,'imagery',3,1,0)
            self.assertTrue(waiting.wait(timeout=1))
            try:
                with self.assertRaises(GeoError) as error: maps.tile('imagery',3,2,0)
                self.assertEqual(error.exception.code,'TILE_BUSY')
            finally: release.set()
            self.assertEqual(first.result(timeout=1)['content_type'],'image/jpeg')
            self.assertEqual(second.result(timeout=1)['content_type'],'image/jpeg')
        self.assertEqual(maps.opener.open.call_count,2)

    def test_waiting_request_reuses_tile_cached_by_earlier_fetch(self):
        maps=MapServices(FIXTURE,PublicSources(),network_concurrency=1,max_waiting=1,queue_timeout=1)
        entered,release,waiting=threading.Event(),threading.Event(),threading.Event()
        semaphore=maps.network_slots
        class SignalWaiting:
            def acquire(self,**kwargs):
                if semaphore.acquire(blocking=False): return True
                waiting.set()
                return semaphore.acquire(**kwargs)
            def release(self): semaphore.release()
        maps.network_slots=SignalWaiting()
        def fetch(*args,**kwargs):
            entered.set()
            release.wait(timeout=1)
            return Response()
        maps.opener.open=Mock(side_effect=fetch)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first=executor.submit(maps.tile,'imagery',3,0,0)
            self.assertTrue(entered.wait(timeout=1))
            second=executor.submit(maps.tile,'imagery',3,0,0)
            self.assertTrue(waiting.wait(timeout=1))
            release.set()
            self.assertFalse(first.result(timeout=1)['cached'])
            self.assertTrue(second.result(timeout=1)['cached'])
        self.assertEqual(maps.opener.open.call_count,1)

    def test_local_layers_do_not_mix_source_types(self):
        config=self.maps.config()
        self.assertEqual(config['center'],[30.32,113.42])
        self.assertEqual(config['crs'],'EPSG:3857')
        self.assertEqual(len(config['basemaps']),2)
        self.assertTrue(all(b['kind']=='imagery' for b in config['basemaps']))
        for basemap in config['basemaps']:
            self.assertEqual(basemap['browser_url'], TILE_TEMPLATES[basemap['id']])
            self.assertEqual(basemap['browser_transport'], 'direct_public_provider')
        self.assertEqual(config['basemaps'][1]['max_native_zoom'],9)
        ids=['agriculture','industry','machinery','water','alerts']
        self.assertFalse(any(layer['source_type']=='synthetic' for layer in config['layers']))
        self.assertFalse(config['capabilities']['synthetic_layers_available'])
        for ident in ids:
            with self.subTest(ident=ident),self.assertRaises(GeoError) as error:self.maps.layer(ident)
            self.assertEqual(error.exception.code,'SYNTHETIC_LAYER_REMOVED')
            self.assertEqual(error.exception.status,410)
        weather=self.maps.layer('weather')['features'][0]
        self.assertEqual(weather['geometry']['coordinates'],[113.45,30.36])
        self.assertEqual(weather['properties']['mode'],'model_forecast_reference')
        self.assertIn('非气象站',weather['properties']['description'])

    def test_public_river_snapshot_provenance_and_clipping(self):
        metadata=json.loads((SOURCE_DIR/'natural_earth_rivers_xiantao.metadata.json').read_text())
        payload=(SOURCE_DIR/'natural_earth_rivers_xiantao.geojson').read_bytes()
        self.assertEqual(hashlib.sha256(payload).hexdigest(),metadata['snapshot_sha256'])
        self.assertEqual(metadata['license'],'Public domain')
        self.assertEqual(metadata['scale'],'1:10,000,000')
        self.assertEqual(metadata['source_type'],'public')
        data=self.maps.layer('rivers')
        self.assertGreater(len(data['features']),0)
        bbox=metadata['clip_bbox']
        for feat in data['features']:
            self.assertEqual(feat['properties']['source_type'],'public')
            for x,y in feat['geometry']['coordinates']:
                self.assertTrue(bbox[0]-1e-9<=x<=bbox[2]+1e-9 and bbox[1]-1e-9<=y<=bbox[3]+1e-9)
        conf=next(x for x in self.maps.config()['layers'] if x['id']=='rivers')
        self.assertFalse(conf['default_visible'])
        self.assertEqual(clip_segment([-1,0],[2,0],[0,-1,1,1]),([0.,0.],[1.,0.]))
        self.assertIsNone(clip_segment([-2,0],[-1,0],[0,-1,1,1]))

    def test_satellite_is_catalog_only_and_errors_are_preserved(self):
        self.maps.public_sources.satellite=Mock(side_effect=GeoError('UPSTREAM_UNAVAILABLE','offline',502))
        with self.assertRaises(GeoError) as error:self.maps.layer('satellite')
        self.assertEqual(error.exception.status,502)
        layer=next(x for x in self.maps.config()['layers'] if x['id']=='satellite')
        self.assertFalse(layer['default_visible']);self.assertEqual(layer['mode'],'catalog_only')
        with self.assertRaises(GeoError):self.maps.layer('made-up')

class MapAPITest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.workspace=Workspace(str(Path(self.temp.name)/'db.sqlite3'))
        self.workspace.map_services.opener.open=Mock(side_effect=lambda *a,**kw:Response())
        self.server=LocalServer(('127.0.0.1',0),make_handler(self.workspace));self.server.quiet=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:%d'%self.server.server_port
    def tearDown(self):
        self.workspace.close();self.server.shutdown();self.server.server_close();self.thread.join(2);self.temp.cleanup()
    def call(self,path):
        try:r=urlopen(self.base+path,timeout=3)
        except HTTPError as e:r=e
        return r.status,r.headers,r.read()
    def test_map_config_layers_and_image_content(self):
        status,_,payload=self.call('/api/map/config');self.assertEqual(status,200);self.assertIn('basemaps',json.loads(payload))
        status,_,payload=self.call('/api/map/layers/rivers');self.assertEqual(status,200);self.assertEqual(json.loads(payload)['type'],'FeatureCollection')
        status,headers,body=self.call('/api/tiles/imagery/12/3338/1685');self.assertEqual(status,200);self.assertEqual(headers['Content-Type'],'image/jpeg');self.assertEqual(body,JPEG)
        self.assertEqual(headers['X-Tile-Cache'],'MISS')
        status,headers,_=self.call('/api/tiles/imagery/12/3338/1685');self.assertEqual(headers['X-Tile-Cache'],'HIT')
    def test_tile_errors_are_structured_not_json_disguised_as_image(self):
        for path,expected in [('/api/tiles/no-source/0/0/0',404),('/api/tiles/imagery/19/0/0',400),('/api/tiles/imagery/0/0/0?url=https://evil.example',400)]:
            status,headers,payload=self.call(path);self.assertEqual(status,expected);self.assertIn('application/json',headers['Content-Type']);self.assertIn('error',json.loads(payload))
        self.workspace.map_services.opener.open=Mock(side_effect=URLError('offline'))
        status,headers,payload=self.call('/api/tiles/imagery/0/0/0');self.assertEqual(status,503);self.assertIn('error',json.loads(payload))

if __name__=='__main__':unittest.main(verbosity=2)
