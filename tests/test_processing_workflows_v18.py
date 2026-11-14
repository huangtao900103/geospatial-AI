"""V18 business workflows over isolated, explicitly synthetic native TIFF fixtures.

These 3 x 3 arrays are test inputs, not satellite observations or product samples.
Every Workspace, source raster and derived artifact lives in TemporaryDirectory.
Temporal dispatch is intercepted before discovery; this suite makes no API calls.
"""
import copy
import hashlib
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

import adaptive_segmentation
from geo_services import GeoError
from server import Workspace


class ProcessingWorkflowsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='v18-workflow-fixture-')
        self.root = Path(self.tmp.name)
        self.w = Workspace(self.root / 'workspace.sqlite3')
        self.gates = []
        self.values = np.array([[.1, .2, .9], [.4, np.nan, .8], [0., .6, 1.]], dtype='float32')
        self.transform = from_origin(500000, 3400000, 10, 10)
        self.bbox = list(transform_bounds('EPSG:32649', 'EPSG:4326', 500000, 3399970, 500030, 3400000))
        directory = self.w.production.output_dir / 'unit_native_ndvi'
        directory.mkdir(parents=True)
        self.source_path = directory / 'analysis.tif'
        bands = {'after_ndvi': self.values, 'common_valid_mask': np.isfinite(self.values).astype('float32'), 'aoi_mask': np.ones((3, 3), dtype='float32')}
        with rasterio.open(self.source_path, 'w', driver='GTiff', width=3, height=3, count=3, dtype='float32', crs='EPSG:32649', transform=self.transform, nodata=float('nan')) as ds:
            for number, (name, array) in enumerate(bands.items(), 1):
                ds.write(array, number)
                ds.set_band_description(number, name)
        self.w.save('production_task', {
            'id': 'unit_native_ndvi', 'name': 'Explicit isolated unit fixture, not a satellite observation',
            'status': 'succeeded', 'created_at': '2026-11-14',
            'plan': {'mode': 'sentinel_cog', 'sampling': 'native', 'goal': 'ndvi', 'index_id': 'ndvi', 'source_id': 'pc_sentinel2_l2a'},
            'result': {'summary': {'index_id': 'ndvi', 'index': {'id': 'ndvi', 'label': 'NDVI', 'threshold_range': [-1, 1]}},
                       'provenance': {'source_type': 'public_satellite_pixels', 'source_id': 'pc_sentinel2_l2a', 'index_id': 'ndvi', 'bbox': self.bbox, 'input_sha256': 'a' * 64, 'crs': 'EPSG:32649', 'source_details': {'scenes': {'after': {'datetime': '2025-11-14T02:30:00Z'}}}},
                       'report': {'limitations': ['Explicit isolated test fixture; these are not real satellite pixels.']}}
        })

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        self.w.close()
        self.tmp.cleanup()

    def body(self, **parameters):
        return {'application_id': 'adaptive_segmentation', 'context': {}, 'parameters': {'source_ref': 'production:unit_native_ndvi', 'direction': 'above', 'min_patch_pixels': 1, **parameters}}

    def temporal_body(self, algorithm='temporal_mean'):
        return {'application_id': 'temporal_analysis', 'context': {'bbox': [114.2, 30.5, 114.21, 30.51], 'period': {'start': '2025-10-01', 'end': '2025-11-30'}},
                'parameters': {'algorithm_id': algorithm, 'index_id': 'ndmi', 'source_id': 'pc_landsat_c2_l2', 'scene_limit': 5, 'cloud': 23}}

    def wait_question(self, id):
        for _ in range(800):
            question = self.w.assistant.get(id)
            if question['status'] != 'processing':
                return question
            time.sleep(.01)
        self.fail('isolated question did not settle')

    def wait_binding(self, id):
        for _ in range(500):
            question = self.w.assistant.get(id)
            if question.get('execution_ref'):
                execution = self.w.executions.get(question['execution_ref']['id'])
                if execution.get('binding'):
                    return execution
                self.assertNotIn(execution['status'], ('failed', 'needs_data', 'unsupported'), execution)
            time.sleep(.01)
        self.fail('isolated execution did not bind a child')

    def gated_segmentation(self):
        entered, gate = threading.Event(), threading.Event()
        self.gates.append(gate)
        original = adaptive_segmentation.run_segmentation

        def run(plan, source_path, directory, check):
            entered.set()
            if not gate.wait(5):
                raise GeoError('UNIT_GATE_TIMEOUT', 'Isolated test worker was not released')
            check()
            return original(plan, source_path, directory, check)

        return entered, gate, run

    def test_otsu_application_freezes_source_parameters_and_native_algorithm(self):
        body = self.body(direction='below', min_patch_pixels=2)
        payload, frozen = self.w.applications.prepare(body)
        expected_context = {'processing_algorithm': 'otsu_segmentation', 'source_type': 'production', 'source_task_id': 'unit_native_ndvi', 'direction': 'below', 'min_patch_pixels': 2}
        self.assertEqual(payload['context'], expected_context)
        plan = self.w.executions.prepare(payload['question'], payload['context'])
        self.assertTrue(plan['executable'], plan)
        self.assertEqual(plan['tool']['id'], 'raster_processing')
        self.assertEqual(plan['tool']['inputs'], {'algorithm_id': 'otsu_segmentation', 'source_task_id': 'unit_native_ndvi', 'direction': 'below', 'min_patch_pixels': 2})
        source = frozen['method']['source_ref']
        self.assertEqual(source['artifact_sha256'], hashlib.sha256(self.source_path.read_bytes()).hexdigest())
        self.assertEqual(source['actual_dates'], {'after': '2025-11-14T02:30:00Z'})
        self.assertEqual(source['artifact_sha256'], plan['source_snapshot']['source']['artifact_sha256'])
        body['parameters']['min_patch_pixels'] = 99
        self.assertEqual(frozen['parameters']['min_patch_pixels'], 2)

    def test_otsu_application_assistant_execution_result_and_named_followups_bind_one_task(self):
        entered, gate, run = self.gated_segmentation()
        body = self.body()
        with patch('adaptive_segmentation.run_segmentation', side_effect=run):
            first = self.w.applications.run(body)
            self.assertTrue(entered.wait(2))
            job = self.wait_binding(first['id'])
            body['parameters'].update(direction='below', min_patch_pixels=8)
            body['context']['bbox'] = [1, 1, 2, 2]
            gate.set()
            question = self.wait_question(first['id'])
        self.assertEqual(question['status'], 'answered', question)
        self.assertEqual(question['application_ref']['parameters']['direction'], 'above')
        self.assertEqual(question['application_ref']['parameters']['min_patch_pixels'], 1)
        self.assertNotIn('bbox', question['context'])
        job = self.w.executions.get(job['id'])
        self.assertEqual(job['status'], 'succeeded', job)
        self.assertEqual(job['plan_snapshot']['tool']['id'], 'raster_processing')
        self.assertEqual(job['context'], question['context'])
        child = self.w.raster_processing.get(job['binding']['task_id'])
        self.assertEqual(child['plan']['request']['direction'], 'above')
        self.assertEqual(child['plan']['request']['min_patch_pixels'], 1)
        self.assertEqual(question['binding']['task_id'], child['id'])
        self.assertEqual(question['answer']['result_ref'], job['result_ref'])
        self.assertEqual(job['result_ref']['type'], 'raster_processing')
        self.assertEqual(job['result_ref']['image_kind'], 'otsu_segmentation')
        self.assertIn(child['id'], job['result_ref']['url'])
        summary = child['result']['summary']
        self.assertEqual(summary['valid_pixel_count'], 8)
        self.assertEqual(summary['native_pixel_count'], 9)
        self.assertAlmostEqual(summary['selected_area_ha'], summary['selected_pixel_count'] * .01)
        self.assertEqual(question['answer']['metrics'][0]['value'], summary['selected_area_ha'])
        self.assertTrue(any(link['url'].endswith('/bundle.zip') for link in question['answer']['downloads']))
        self.assertEqual(len(question['answer']['downloads']), 6)
        for text in ('Otsu结果来自哪里？', '下载自适应阈值分区结果', 'Otsu结果精度如何？'):
            with self.subTest(text=text):
                followup = self.wait_question(self.w.assistant.create({'question': text, 'context': {'reference_question_id': question['id']}})['id'])
                self.assertEqual(followup['status'], 'answered', followup)
                self.assertEqual(followup['binding']['task_id'], child['id'])
                self.assertEqual(followup['answer']['map_effect'], 'none')
                self.assertEqual(followup['answer']['result_ref']['type'], 'raster_processing')
        self.assertEqual(len(self.w.raster_processing.list()), 1)

    def test_temporal_templates_and_assistant_pass_all_parameters_to_processing_not_single_scene(self):
        for algorithm in ('temporal_median', 'temporal_mean', 'temporal_trend'):
            with self.subTest(algorithm=algorithm):
                body = self.temporal_body(algorithm)
                payload, frozen = self.w.applications.prepare(body)
                plan = self.w.executions.prepare(payload['question'], payload['context'])
                self.assertTrue(plan['executable'], plan)
                expected = {'algorithm_id': algorithm, 'source_id': 'pc_landsat_c2_l2', 'index_id': 'ndmi', 'bbox': body['context']['bbox'], 'start': '2025-10-01', 'end': '2025-11-30', 'scene_limit': 5, 'cloud': 23}
                self.assertEqual(plan['tool']['id'], 'raster_processing')
                self.assertEqual(plan['tool']['inputs'], expected)
                self.assertEqual(frozen['method']['processing_algorithm_id'], algorithm)
                with patch.object(self.w.raster_processing, 'create', side_effect=GeoError('UNIT_NO_REMOTE', 'Temporal dispatch intercepted before any remote discovery')) as dispatched:
                    question = self.wait_question(self.w.applications.run(body)['id'])
                self.assertEqual(question['status'], 'failed', question)
                dispatched.assert_called_once_with(expected)
                job = self.w.executions.get(question['execution_ref']['id'])
                self.assertEqual(job['context'], payload['context'])
                self.assertEqual(job['plan_snapshot']['tool']['inputs'], expected)
                self.assertEqual(job['error']['code'], 'UNIT_NO_REMOTE')
        self.assertEqual([t['id'] for t in self.w.production.list()], ['unit_native_ndvi'])
        self.assertFalse(self.w.raster_processing.list())

    def test_conflicting_algorithms_indices_dates_and_unsupported_business_are_blocked(self):
        payload, _ = self.w.applications.prepare(self.temporal_body())
        for text, change in (
            ('对所选区域NDMI执行中位数合成', {}),
            ('对所选区域NDVI执行时段均值合成', {}),
            ('对所选区域2025年9月NDMI执行时段均值合成', {}),
            ('对所选区域2025-10-01至2025-12-31的NDMI执行时段均值合成', {}),
            ('对所选区域NDMI执行时序趋势和均值合成', {}),
            (payload['question'], {'source_task_id': 'unit_native_ndvi'}),
            ('统计全市农田面积并执行均值合成', {}),
        ):
            with self.subTest(text=text, change=change):
                self.assertFalse(self.w.executions.prepare(text, {**payload['context'], **change})['executable'])
        context = self.w.applications.prepare(self.body())[0]['context']
        for text, change in (
            ('对NDRE进行Otsu自动阈值分区', {}),
            ('对2024年的NDVI进行Otsu自动阈值分区', {}),
            ('对今年NDVI进行Otsu自动阈值分区', {}),
            ('对NDVI进行Otsu分区，阈值为0.5', {}),
            ('对NDVI进行Otsu分区，保留低值', {}),
            ('对NDVI进行Otsu分区', {'bbox': [113., 30., 113.01, 30.01]}),
            ('对NDVI进行Otsu分区', {'period': {'start': '2025-11-01', 'end': '2025-11-30'}}),
        ):
            with self.subTest(text=text, change=change):
                self.assertFalse(self.w.executions.prepare(text, {**context, **change})['executable'])
        missing = self.w.executions.prepare('对NDVI进行Otsu自适应阈值分区', {})
        self.assertFalse(missing['executable'])
        self.assertIn('source_task_id', missing['missing_fields'])
        self.assertFalse(self.w.raster_processing.list())

    def test_otsu_same_year_month_and_exact_date_conflicts_cannot_relabel_archived_source(self):
        context = self.w.applications.prepare(self.body())[0]['context']
        for text in ('对2025年10月NDVI进行Otsu自适应阈值分区', '对2025-10-01 NDVI进行Otsu自适应阈值分区', '对2025-11-99 NDVI进行Otsu自适应阈值分区'):
            with self.subTest(text=text):
                self.assertFalse(self.w.executions.prepare(text, context)['executable'])
        self.assertTrue(self.w.executions.prepare('对2025年11月NDVI进行Otsu自适应阈值分区', context)['executable'])

    def test_otsu_explicit_high_direction_cannot_be_silently_replaced_by_low_direction(self):
        context = self.w.applications.prepare(self.body(direction='below'))[0]['context']
        plan = self.w.executions.prepare('对NDVI进行Otsu自适应阈值分区，保留高值', context)
        self.assertFalse(plan['executable'], plan)

    def test_reuse_keeps_original_results_but_source_bytes_change_requires_new_task(self):
        first = self.wait_question(self.w.applications.run(self.body())['id'])
        self.assertEqual(first['status'], 'answered', first)
        second = self.wait_question(self.w.applications.run(self.body())['id'])
        self.assertEqual(second['status'], 'answered', second)
        self.assertEqual(first['binding']['task_id'], second['binding']['task_id'])
        self.assertTrue(second['binding']['reused'])
        child = self.w.raster_processing.get(first['binding']['task_id'])
        inputs = self.w.executions.get(first['execution_ref']['id'])['plan_snapshot']['tool']['inputs']
        old_key = self.w.executions.input_key('raster_processing', inputs)
        old_sha = child['plan']['source']['artifact_sha256']
        old_result = copy.deepcopy(child['result'])
        with rasterio.open(self.source_path, 'r+') as ds:
            changed = ds.read(1)
            changed[0, 0] = .99
            ds.write(changed, 1)
        new_key = self.w.executions.input_key('raster_processing', inputs)
        self.assertNotEqual(new_key, old_key)
        self.assertFalse(self.w.executions.compatible_child('raster_processing', child, inputs, new_key))
        third = self.wait_question(self.w.applications.run(self.body())['id'])
        self.assertEqual(third['status'], 'answered', third)
        self.assertNotEqual(third['binding']['task_id'], first['binding']['task_id'])
        self.assertFalse(third['binding']['reused'])
        new_child = self.w.raster_processing.get(third['binding']['task_id'])
        self.assertNotEqual(new_child['plan']['source']['artifact_sha256'], old_sha)
        self.assertEqual(self.w.raster_processing.get(child['id'])['result'], old_result)
        self.assertEqual(self.w.assistant.get(first['id'])['answer'], first['answer'])
        with self.assertRaises(GeoError) as error:
            self.w.raster_processing.verify_source(child['plan'], lambda: None)
        self.assertEqual(error.exception.code, 'PROCESSING_SOURCE_CHANGED')

    def test_owned_cancel_stops_child_and_retry_preserves_frozen_parameters(self):
        entered, gate, run = self.gated_segmentation()
        with patch('adaptive_segmentation.run_segmentation', side_effect=run):
            question = self.w.applications.run(self.body(direction='below', min_patch_pixels=2))
            self.assertTrue(entered.wait(2))
            job = self.wait_binding(question['id'])
            self.assertTrue(job['binding']['owned'])
            child_id = job['binding']['task_id']
            cancelled = self.w.assistant.cancel(question['id'])
            self.assertEqual(cancelled['status'], 'cancelled')
            self.assertEqual(self.w.executions.get(job['id'])['status'], 'cancelled')
            self.assertEqual(self.w.raster_processing.get(child_id)['status'], 'cancelled')
            self.assertIsNone(self.w.assistant.get(question['id'])['answer'])
            gate.set()
            retried = self.wait_question(self.w.assistant.retry(question['id'])['id'])
        self.assertEqual(retried['status'], 'answered', retried)
        self.assertNotEqual(retried['binding']['task_id'], child_id)
        self.assertEqual(retried['application_ref'], cancelled['application_ref'])
        self.assertEqual(retried['context'], cancelled['context'])
        self.assertEqual(self.w.raster_processing.get(retried['binding']['task_id'])['plan']['request'], self.w.raster_processing.get(child_id)['plan']['request'])

    def test_source_record_hash_change_also_invalidates_reuse_when_tiff_bytes_are_unchanged(self):
        first = self.wait_question(self.w.applications.run(self.body())['id'])
        self.assertEqual(first['status'], 'answered', first)
        child = self.w.raster_processing.get(first['binding']['task_id'])
        old_sha = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
        source = self.w.get('production_task', 'unit_native_ndvi')
        source['result']['provenance']['input_sha256'] = 'b' * 64
        self.w.save('production_task', source)
        second = self.wait_question(self.w.applications.run(self.body())['id'])
        self.assertEqual(second['status'], 'answered', second)
        self.assertNotEqual(second['binding']['task_id'], first['binding']['task_id'])
        self.assertFalse(second['binding']['reused'])
        new_child = self.w.raster_processing.get(second['binding']['task_id'])
        self.assertEqual(old_sha, hashlib.sha256(self.source_path.read_bytes()).hexdigest())
        self.assertEqual(child['plan']['source']['artifact_sha256'], new_child['plan']['source']['artifact_sha256'])
        self.assertNotEqual(child['plan']['source']['record_fingerprint'], new_child['plan']['source']['record_fingerprint'])
        self.assertNotEqual(child['plan']['input_fingerprint'], new_child['plan']['input_fingerprint'])
        with self.assertRaises(GeoError) as error:
            self.w.raster_processing.verify_source(child['plan'], lambda: None)
        self.assertEqual(error.exception.code, 'PROCESSING_SOURCE_CHANGED')

    def test_cancelling_reused_request_keeps_owner_child_running(self):
        entered, gate, run = self.gated_segmentation()
        with patch('adaptive_segmentation.run_segmentation', side_effect=run):
            owner = self.w.applications.run(self.body())
            self.assertTrue(entered.wait(2))
            owner_job = self.wait_binding(owner['id'])
            shared = self.w.applications.run(self.body())
            shared_job = self.wait_binding(shared['id'])
            self.assertEqual(shared_job['binding']['task_id'], owner_job['binding']['task_id'])
            self.assertTrue(shared_job['binding']['reused'])
            self.assertFalse(shared_job['binding']['owned'])
            self.w.assistant.cancel(shared['id'])
            self.assertIn(self.w.raster_processing.get(owner_job['binding']['task_id'])['status'], ('queued', 'running'))
            gate.set()
            owner = self.wait_question(owner['id'])
        self.assertEqual(owner['status'], 'answered', owner)
        self.assertEqual(self.w.assistant.get(shared['id'])['status'], 'cancelled')
        self.assertEqual(len(self.w.raster_processing.list()), 1)


if __name__ == '__main__':
    unittest.main()
