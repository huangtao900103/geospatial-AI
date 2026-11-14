"""Deterministic cache contracts; no remote acquisition or timing benchmarks."""
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from importlib.util import find_spec
from raster_cache import RasterWindowCache
from geo_services import GeoError

@unittest.skipUnless(find_spec('numpy'),'numpy runtime required')
class RasterCacheTest(unittest.TestCase):
    def value(self,n=4):
        import numpy as np
        return {'raw':np.arange(n,dtype='float32').reshape(1,n),'evidence':{'window_sha256':'test-only'}}
    def test_hit_has_same_raw_hash_and_original_fetch_time_but_no_mutation(self):
        import numpy as np
        c=RasterWindowCache();original=self.value();a,hit=c.get_or_load({'href':'https://example.org/a.tif'},lambda:original);self.assertFalse(hit)
        original['raw'][:]=-5;original['evidence']['window_sha256']='changed';a['evidence']['window_sha256']='caller';self.assertFalse(a['raw'].flags.writeable)
        with self.assertRaises(ValueError):a['raw'][0,0]=8
        b,hit=c.get_or_load({'href':'https://example.org/a.tif'},lambda:self.fail('should not load'));self.assertTrue(hit);np.testing.assert_array_equal(b['raw'],[[0,1,2,3]])
        self.assertEqual(b['evidence']['window_sha256'],'test-only');self.assertEqual(a['cache']['fetched_at'],b['cache']['fetched_at']);self.assertFalse(b['cache']['source_revalidated']);self.assertEqual(c.stats()['current_bytes'],16)
    def test_key_separates_sources_windows_and_read_policy(self):
        base={'href':'https://example.org/a.tif','window':[1,2,3,4],'crs':'EPSG:32649','transform':[10,0,0,0,-10,0],'nodata':0,'dtype':'uint16','boundless':True,'masked':True,'read_dtype':'float32'}
        keys={RasterWindowCache.key(base)}
        for field,value in [('href','https://example.org/b.tif'),('window',[0,2,3,4]),('dtype','float32'),('masked',False)]:keys.add(RasterWindowCache.key({**base,field:value}))
        self.assertEqual(len(keys),5)
    def test_unsigned_finite_keys_only(self):
        c=RasterWindowCache()
        for key in ({'href':'https://x/a.tif?sig=secret'},{'token':'secret'},{'href':'https://me:secret@x/a.tif'},{'nodata':float('nan')}):
            with self.subTest(key=list(key)),self.assertRaises(ValueError):c.get_or_load(key,lambda:self.fail('invalid key loader'))
    def test_expiry_reloads_and_budget_evicts_lru(self):
        now=[0.];c=RasterWindowCache(max_bytes=32,ttl_seconds=10,clock=lambda:now[0])
        c.get_or_load('a',self.value);c.get_or_load('b',self.value);c.get_or_load('a',self.value);c.get_or_load('c',self.value)
        self.assertEqual(c.stats()['evictions'],1);self.assertLessEqual(c.stats()['current_bytes'],32)
        _,hit=c.get_or_load('a',self.value);self.assertTrue(hit);now[0]=11
        _,hit=c.get_or_load('a',self.value);self.assertFalse(hit);self.assertEqual(c.stats()['expirations'],2)
    def test_entry_count_and_oversize_do_not_overflow(self):
        c=RasterWindowCache(max_bytes=1024,max_entries=1);c.get_or_load('a',self.value);c.get_or_load('b',self.value);self.assertEqual(c.stats()['entries'],1)
        c=RasterWindowCache(max_bytes=1);c.get_or_load('large',self.value);_,hit=c.get_or_load('large',self.value);self.assertFalse(hit);self.assertEqual(c.stats()['entries'],0);self.assertEqual(c.stats()['current_bytes'],0);self.assertEqual(c.stats()['oversize_skips'],2)
    def test_singleflight_only_one_loader_and_waiters_receive_same_data(self):
        c=RasterWindowCache();gate=threading.Event();started=threading.Event();calls=[]
        def load():calls.append(1);started.set();self.assertTrue(gate.wait(2));return self.value()
        with ThreadPoolExecutor(max_workers=4) as pool:
            first=pool.submit(c.get_or_load,'a',load);self.assertTrue(started.wait(1));rest=[pool.submit(c.get_or_load,'a',load) for _ in range(3)]
            deadline=time.monotonic()+1
            while c.stats()['waits']<3 and time.monotonic()<deadline:time.sleep(.005)
            gate.set();results=[first.result(timeout=2)]+[f.result(timeout=2) for f in rest]
        self.assertEqual(len(calls),1);self.assertEqual([hit for _,hit in results],[False,True,True,True]);self.assertEqual(c.stats()['inflight'],0)
    def test_loader_exception_wakes_waiters_and_is_not_cached(self):
        c=RasterWindowCache();gate=threading.Event();started=threading.Event()
        def load():started.set();gate.wait(2);raise ValueError('controlled failure')
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(c.get_or_load,'a',load);self.assertTrue(started.wait(1));b=pool.submit(c.get_or_load,'a',load)
            deadline=time.monotonic()+1
            while not c.stats()['waits'] and time.monotonic()<deadline:time.sleep(.005)
            gate.set()
            for f in (a,b):
                with self.assertRaises(ValueError):f.result(timeout=2)
        self.assertEqual(c.stats()['inflight'],0);self.assertEqual(c.stats()['entries'],0);_,hit=c.get_or_load('a',self.value);self.assertFalse(hit)
    def test_cancelled_waiter_leaves_shared_leader_running(self):
        c=RasterWindowCache();gate=threading.Event();started=threading.Event();cancel=threading.Event()
        def load():started.set();gate.wait(2);return self.value()
        def check():
            if cancel.is_set():raise GeoError('CANCELLED','controlled waiter cancellation')
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(c.get_or_load,'a',load);self.assertTrue(started.wait(1));b=pool.submit(c.get_or_load,'a',load,check)
            cancel.set()
            with self.assertRaises(GeoError):b.result(timeout=1)
            gate.set();self.assertFalse(a.result(timeout=2)[1])
        self.assertTrue(c.get_or_load('a',self.value)[1])
    def test_cancelled_leader_wakes_live_waiter_to_load_again(self):
        c=RasterWindowCache();gate=threading.Event();started=threading.Event()
        def cancelled():started.set();gate.wait(2);raise GeoError('CANCELLED','leader cancelled')
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(c.get_or_load,'a',cancelled);self.assertTrue(started.wait(1));b=pool.submit(c.get_or_load,'a',self.value)
            deadline=time.monotonic()+1
            while not c.stats()['waits'] and time.monotonic()<deadline:time.sleep(.005)
            gate.set()
            with self.assertRaises(GeoError):a.result(timeout=2)
            self.assertFalse(b.result(timeout=2)[1])
        self.assertEqual(c.stats()['misses'],2);self.assertEqual(c.stats()['inflight'],0)

if __name__=='__main__':unittest.main()
