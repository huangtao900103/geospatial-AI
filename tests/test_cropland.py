"""Real raster tests for bounded cropland extraction (synthetic fixtures labelled as tests)."""
import copy
import csv
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.analysis.cropland_services import (CroplandServices, make_plan, selection_boundary,
                               vectorize_cropland, capabilities, MAX_PATCHES)
from backend.core.geo_services import GeoError
from backend.analysis.landcover_services import account_native, available, ellipsoid_row_pixel_area


class MemoryStore:
    def __init__(self, root):
        self.db_path = str(Path(root)/'test.sqlite3')
        self.lock = threading.RLock()
        self.records = {}

    def list(self, kind):
        with self.lock:
            return [copy.deepcopy(v) for (k, _), v in self.records.items() if k == kind]

    def get(self, kind, ident):
        with self.lock:
            return copy.deepcopy(self.records[(kind, ident)])

    def save(self, kind, obj):
        with self.lock:
            self.records[(kind, obj['id'])] = copy.deepcopy(obj)
        return copy.deepcopy(obj)


class PlanTest(unittest.TestCase):
    def test_requirement_defaults_explicit_historical_product(self):
        plan = make_plan({'bbox': [113.3, 30.3, 113.31, 30.31],
                          'requirement': '把当前范围内的农田全部标注出来'})
        self.assertEqual(plan['reference_year'], 2021)
        self.assertEqual(plan['product']['id'], 'worldcover_2021_v200')
        self.assertEqual(plan['operation'], 'published_classification_extraction')
        self.assertFalse(plan['statistics_resampled'])

    def test_missing_unsafe_large_aoi_rejected_before_network(self):
        for bbox in [None, [], [0, 0, .1, .01], [0, 0, .01, .1],
                     [True, 0, .01, .01], [float('inf'), 0, .01, .01],
                     [1, 0, 0, .01], [0, 86, .01, 86.01], [180, 0, 180.01, .01]]:
            with self.subTest(bbox=bbox), self.assertRaises(GeoError):
                make_plan({'bbox': bbox})
        with self.assertRaises(GeoError):
            make_plan({'bbox': [113.3, 30.3, 113.31, 30.31], 'url': 'https://evil.test/a.tif'})

    def test_current_year_and_business_semantics_not_silently_changed(self):
        for requirement in ['标注2026年农田', '识别今年的农田', '统计当前农田',
                            '利用最新影像识别农田', '农田确权', '高标准农田',
                            '全省农田', '全市农田', '水稻识别', '农田1999', '去年农田',
                            '当前的农田', '水稻农田', '官方农田', '农田播种面积',
                            '2021年10月农田', '农田二〇二六年']:
            with self.subTest(requirement=requirement), self.assertRaises(GeoError):
                make_plan({'bbox': [113.3, 30.3, 113.31, 30.31], 'requirement': requirement})
        with self.assertRaises(GeoError):
            make_plan({'bbox': [113.3, 30.3, 113.31, 30.31], 'product': 'worldcover_2020'})


@unittest.skipUnless(available(), 'rasterio/numpy/Pillow required')
class NativeTest(unittest.TestCase):
    def setUp(self):
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.res = 1/12000
        self.left, self.top = 113., 30.5
        self.bbox = [self.left, self.top-5*self.res, self.left+5*self.res, self.top]
        # One ring-shaped patch with a hole, two single-cell patches, and nodata.
        self.values = np.asarray([[40, 40, 40, 10, 40],
                                  [40, 80, 40, 10, 10],
                                  [40, 40, 40, 10, 10],
                                  [10, 10, 10, 40, 10],
                                  [0, 0, 10, 10, 10]], dtype='uint8')
        self.path = self.root/'test-only.tif'
        with rasterio.open(self.path, 'w', driver='GTiff', width=5, height=5, count=1,
                           dtype='uint8', crs='EPSG:4326', nodata=0,
                           transform=from_origin(self.left, self.top, self.res, self.res)) as ds:
            ds.write(self.values, 1)
        self.boundary, self.metadata = selection_boundary(self.bbox)

    def tearDown(self):
        self.tmp.cleanup()

    def account(self):
        return account_native(self.boundary, self.metadata, self.root/'output', source_urls=[str(self.path)])

    def test_all_patches_and_holes_area_match_independent_row_formula(self):
        result = self.account()
        vector = vectorize_cropland(result['native_path'], self.root/'output')
        self.assertEqual(vector['patch_count'], 3)
        self.assertEqual(vector['pixel_count'], 10)
        ring = next(f for f in vector['geojson']['features'] if f['properties']['pixel_count'] == 8)
        self.assertEqual(len(ring['geometry']['coordinates']), 2)
        independent = sum(int((row == 40).sum())*ellipsoid_row_pixel_area(
            self.top-r*self.res, self.top-(r+1)*self.res, self.res)
            for r, row in enumerate(self.values))
        self.assertAlmostEqual(vector['area_m2'], independent, places=5)
        self.assertAlmostEqual(vector['area_m2']/10000, result['summary']['cropland_ha'], places=10)
        self.assertEqual(sum(f['properties']['pixel_count'] for f in vector['geojson']['features']), 10)
        with (self.root/'output'/'patches.csv').open(encoding='utf-8-sig') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(int(row['pixel_count']) for row in rows), 10)
        self.assertEqual(result['summary']['nodata_pixel_count'], 2)
        self.assertLess(result['summary']['coverage_ratio'], 1)

    def test_diagonal_cells_are_separate_under_four_connectivity(self):
        import numpy as np
        import rasterio
        with rasterio.open(self.path, 'r+') as ds:
            data = np.full((5, 5), 10, dtype='uint8')
            data[0, 0] = 40
            data[1, 1] = 40
            ds.write(data, 1)
        result = self.account()
        vector = vectorize_cropland(result['native_path'], self.root/'output')
        self.assertEqual(vector['patch_count'], 2)
        self.assertEqual(vector['pixel_count'], 2)

    def test_zero_cropland_remains_zero_no_fake_features(self):
        import numpy as np
        import rasterio
        with rasterio.open(self.path, 'r+') as ds:
            ds.write(np.full((5, 5), 10, dtype='uint8'), 1)
        result = self.account()
        vector = vectorize_cropland(result['native_path'], self.root/'output')
        self.assertEqual(vector['geojson']['features'], [])
        self.assertEqual(vector['area_m2'], 0)
        self.assertEqual(vector['patch_count'], 0)

    def test_complexity_limit_fails_without_publishing_partial_geojson(self):
        result = self.account()
        with patch('backend.analysis.cropland_services.MAX_PATCHES', 1), self.assertRaises(GeoError) as raised:
            vectorize_cropland(result['native_path'], self.root/'limit')
        self.assertEqual(raised.exception.code, 'PATCH_LIMIT')
        self.assertFalse((self.root/'limit'/'cropland.geojson').exists())

    def test_cancel_vectorization_stops_without_fake_output(self):
        result = self.account()
        def cancel():
            raise GeoError('CANCELLED', 'cancelled')
        with self.assertRaises(GeoError):
            vectorize_cropland(result['native_path'], self.root/'cancel', check=cancel)
        self.assertFalse((self.root/'cancel'/'cropland.geojson').exists())

    def test_native_source_coverage_gap_fails(self):
        boundary, metadata = selection_boundary([self.left-self.res, self.bbox[1], self.bbox[2], self.top])
        with self.assertRaises(GeoError) as raised:
            account_native(boundary, metadata, self.root/'gap', source_urls=[str(self.path)])
        self.assertEqual(raised.exception.code, 'SOURCE_COVERAGE_GAP')

    def test_real_service_lifecycle_and_download_allowlist(self):
        store = MemoryStore(self.root)
        service = CroplandServices(store, source_paths=[str(self.path)])
        try:
            task = service.create({'bbox': self.bbox, 'requirement': '提取选区2021年耕作地参考图斑'})
            for _ in range(500):
                task = service.get(task['id'])
                if task['status'] in ('succeeded', 'failed', 'cancelled'):
                    break
                time.sleep(.01)
            self.assertEqual(task['status'], 'succeeded', task)
            result = task['result']
            self.assertEqual(result['summary']['patch_count'], 3)
            self.assertEqual(result['summary']['reference_year'], 2021)
            self.assertEqual(result['bbox'], self.bbox)
            self.assertFalse(result['provenance']['statistics_resampled'])
            self.assertEqual(result['provenance']['source_type'], 'public_classification_product')
            for artifact in result['artifacts']:
                self.assertTrue(service.artifact(task['id'], artifact['url'].split('/')[-1]).is_file())
            with self.assertRaises(GeoError):
                service.artifact(task['id'], '../test.sqlite3')
            with self.assertRaises(GeoError):
                service.cancel(task['id'])
        finally:
            service.close()


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(self.tmp.name)
        self.service = CroplandServices(self.store)

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_busy_cancel_and_retry_keep_snapshot(self):
        body = {'bbox': [113.3, 30.3, 113.31, 30.31], 'requirement': '选区2021年耕作地参考图斑'}
        with patch('backend.analysis.cropland_services.threading.Thread'):
            task = self.service.create(body)
            with self.assertRaises(GeoError) as raised:
                self.service.create(body)
            self.assertEqual(raised.exception.code, 'CROPLAND_BUSY')
            self.assertEqual(self.service.cancel(task['id'])['status'], 'cancelled')
            self.assertFalse(self.service.change(task['id'], lambda t: t.update(status='succeeded')))
            new = self.service.retry(task['id'])
            self.assertNotEqual(new['id'], task['id'])
            self.assertEqual(new['snapshot'], task['snapshot'])
            self.service.cancel(new['id'])

    def test_recovery_fails_interrupted_jobs(self):
        self.store.save('cropland_task', {'id': 'cropland-old', 'status': 'running',
            'nodes': [{'status': 'running'}, {'status': 'pending'}], 'logs': []})
        recovered = CroplandServices(self.store).get('cropland-old')
        self.assertEqual(recovered['status'], 'failed')
        self.assertEqual(recovered['error']['code'], 'INTERRUPTED')
        self.assertTrue(all(node['status'] == 'failed' for node in recovered['nodes']))


if __name__ == '__main__':
    unittest.main()
