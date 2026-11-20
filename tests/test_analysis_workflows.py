"""Isolated real Otsu chains over explicit 3x3 synthetic test rasters.

No production DB, HTTP server, model, external catalog or network is used. The
production acquisition boundary is a fixture; Otsu, source checks, TIFF/GIS
artifacts and execution compatibility use the real registered implementations.
"""
import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from backend.core.geo_services import GeoError
from backend.analysis.production_services import make_plan
from backend.analysis.native_raster import VERSION as NATIVE_VERSION
from backend.analysis.spectral_indices import specification
from backend.analysis.raster_processing_services import RasterProcessingServices
from backend.agriculture.agriculture_services import AgricultureServices
from backend.workflows.execution_services import ExecutionServices
from backend.workflows.analysis_workflows import AnalysisWorkflowServices, KIND
import backend.analysis.adaptive_segmentation as segmentation


class MemoryStore:
    def __init__(self, root):
        self.db_path = root / 'isolated-no-database.sqlite3'
        self.lock = threading.RLock()
        self.records = {}

    def save(self, kind, value):
        with self.lock:
            self.records[kind, value['id']] = copy.deepcopy(value)
            return copy.deepcopy(value)

    def get(self, kind, ident):
        with self.lock:
            if (kind, ident) not in self.records:
                raise GeoError('NOT_FOUND', 'No isolated fixture', 404)
            return copy.deepcopy(self.records[kind, ident])

    def list(self, kind):
        with self.lock:
            return [copy.deepcopy(value) for (record_kind, _), value in self.records.items() if record_kind == kind]


class ProductionBoundary:
    """Explicit acquisition fixture; never claims these arrays are observations."""
    def __init__(self, store, root):
        self.store, self.output_dir = store, root / 'production'
        self.create_calls = []
        self.observation = {'id': 'obs_unit', 'metadata_sha256': 'a' * 64, 'collection': 'unit-s2',
                            'item_id': 'unit-scene', 'source_id': 'pc_sentinel2_l2a'}
        self.transform = from_origin(500000, 3400000, 10, 10)
        self.bbox = list(transform_bounds('EPSG:32649', 'EPSG:4326', 500000, 3399970, 500030, 3400000))

    def get(self, ident):
        return self.store.get('production_task', ident)

    def list(self):
        return self.store.list('production_task')

    def resolve_input(self, body):
        body = copy.deepcopy(body)
        if body.get('observation_id'):
            if body['observation_id'] != self.observation['id']:
                raise GeoError('NOT_FOUND', 'No isolated observation', 404)
            if body.get('source_id', 'auto') not in ('auto', self.observation['source_id']):
                raise GeoError('SOURCE_CONFLICT', 'Pinned source conflict', 422)
            body.update(source_id=self.observation['source_id'], scene_id=self.observation['item_id'],
                        observation_ref={key: self.observation[key] for key in ('id', 'metadata_sha256', 'collection', 'item_id')})
        return body

    def seed(self, ident='unit_native', request=None):
        request = request or {'requirement': '2025年11月NDVI原生分析', 'mode': 'sentinel_cog',
                             'sampling': 'native', 'goal': 'ndvi', 'bbox': self.bbox,
                             'periods': {'before': ['2025-10-31', '2025-10-31'],
                                         'after': ['2025-11-01', '2025-11-30']}, 'index_id': 'ndvi'}
        plan = make_plan(self.resolve_input(request))
        index = plan['index_id']
        values = np.array([[.1, .2, .9], [.4, np.nan, .8], [0., .6, 1.]], dtype='float32')
        directory = self.output_dir / ident
        directory.mkdir(parents=True)
        with rasterio.open(directory / 'analysis.tif', 'w', driver='GTiff', width=3, height=3,
                           count=3, dtype='float32', crs='EPSG:32649', transform=self.transform, nodata=float('nan')) as ds:
            for number, (name, values) in enumerate([(f'after_{index}', values), ('common_valid_mask', np.isfinite(values).astype('float32')), ('aoi_mask', np.ones((3, 3), dtype='float32'))], 1):
                ds.write(values, number)
                ds.set_band_description(number, name)
        Image.new('RGBA', (3, 3), (0, 0, 0, 0)).save(directory / 'overlay.png')
        result = {'summary': {'sampling': 'native', 'index_id': index, 'index': specification(index)},
                  'provenance': {'source_type': 'public_satellite_pixels', 'sampling': 'native',
                                 'algorithm_version': NATIVE_VERSION, 'algorithm': specification(index),
                                 'raster_evidence': {'fixture': True}, 'source_id': plan['source_id'],
                                 'bbox': self.bbox, 'input_sha256': 'b' * 64,
                                 'source_details': {'scenes': {'after': {'datetime': '2025-11-14T02:30:00Z'}}}},
                  'report': {'limitations': ['Explicit isolated synthetic test raster, not a satellite observation']}}
        (directory / 'result.json').write_text(json.dumps(result, allow_nan=False), 'utf-8')
        task = {'id': ident, 'status': 'succeeded', 'progress': 100, 'plan': plan, 'result': result}
        self.store.save('production_task', task)
        return task

    def create(self, body):
        self.create_calls.append(copy.deepcopy(body))
        return self.seed('unit_generated_' + str(len(self.create_calls)), body)

    def artifact(self, ident, name):
        if self.get(ident)['status'] != 'succeeded':
            raise GeoError('ARTIFACT_NOT_READY', 'Fixture not completed', 404)
        path = self.output_dir / ident / name
        if not path.is_file():
            raise GeoError('ARTIFACT_MISSING', 'Fixture artifact missing', 404)
        return path

    def cancel(self, ident):
        task = self.get(ident)
        task['status'] = 'cancelled'
        return self.store.save('production_task', task)


def seed_native(store, root, ident='production_browser_spectral'):
    """Seed an explicitly synthetic native fixture in an isolated workspace only.

    root must be the caller's TemporaryDirectory / isolated DB parent. This helper
    does not initialize services, a server or a database and performs no I/O
    outside that root. Never use it as business/demo satellite evidence.
    """
    return ProductionBoundary(store, Path(root)).seed(ident)


class AnalysisWorkflowsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bounded-analysis-chain-')
        self.root = Path(self.tmp.name)
        self.store = MemoryStore(self.root)
        self.store.production = ProductionBoundary(self.store, self.root)
        self.store.agriculture = AgricultureServices(self.store)
        self.store.raster_processing = RasterProcessingServices(self.store)
        self.store.executions = ExecutionServices(self.store)
        self.service = AnalysisWorkflowServices(self.store)
        self.store.analysis_workflows = self.service
        self.store.production.seed()
        self.gates = []
        self.extra_services = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        self.service.close()
        for service in self.extra_services:
            service.close()
        self.store.raster_processing.close()
        self.store.agriculture.close()
        self.store.executions.close()
        self.tmp.cleanup()

    def body(self, **parameters):
        return {'template_id': 'spectral_partition', 'context': {'source_task_id': 'unit_native'},
                'parameters': parameters}

    def observation_body(self, index='ndvi'):
        return {'template_id': 'spectral_partition', 'context': {'observation_id': 'obs_unit',
                'bbox': self.store.production.bbox, 'period': {'start': '2025-11-01', 'end': '2025-11-30'}},
                'parameters': {'index_id': index}}

    def wait(self, ident, service=None):
        service = service or self.service
        for _ in range(600):
            job = service.get(ident)
            if job['status'] not in ('queued', 'running'):
                return job
            time.sleep(.01)
        self.fail('isolated analysis chain did not settle')

    def wait_binding(self, ident, position=1):
        for _ in range(400):
            job = self.service.get(ident)
            if job['nodes'][position].get('binding'):
                return job
            self.assertIn(job['status'], ('queued', 'running'), job)
            time.sleep(.01)
        self.fail('isolated child was not bound')

    def gate_segmentation(self):
        entered, gate = threading.Event(), threading.Event()
        self.gates.append(gate)
        original = segmentation.run_segmentation

        def run(*args, **kwargs):
            entered.set()
            if not gate.wait(5):
                raise GeoError('UNIT_GATE_TIMEOUT', 'Isolated test gate timeout')
            return original(*args, **kwargs)
        return entered, gate, patch.object(segmentation, 'run_segmentation', side_effect=run)

    def test_real_existing_source_chain_produces_quantities_gis_and_evidence(self):
        plan = self.service.prepare(self.body())
        self.assertEqual([n['tool_id'] for n in plan['nodes']], ['production_result', 'raster_processing'])
        self.assertEqual(self.store.raster_processing.list(), [])
        job = self.wait(self.service.create(self.body())['id'])
        self.assertEqual(job['status'], 'succeeded', job)
        self.assertEqual(job['summary']['valid_pixel_count'], 8)
        self.assertAlmostEqual(job['summary']['unknown_area_ha'], .01)
        self.assertEqual(job['result_ref']['type'], 'raster_processing')
        self.assertTrue(job['result']['geojson']['features'])
        self.assertEqual(len(job['evidence']), 2)
        self.assertTrue(all(len(a['sha256']) == 64 for evidence in job['evidence'] for a in evidence['artifacts']))
        self.assertEqual(self.store.production.create_calls, [])

    def test_pinned_observation_runs_both_registered_services_for_supported_indices(self):
        for index in ('ndvi', 'ndwi', 'ndmi'):
            with self.subTest(index=index):
                job = self.wait(self.service.create(self.observation_body(index))['id'])
                self.assertEqual(job['status'], 'succeeded', job)
                self.assertEqual(job['summary']['index_id'], index)
                first = job['nodes'][0]
                self.assertEqual(first['tool_id'], 'sentinel_native')
                self.assertEqual(first['evidence']['source']['index_id'], index)
                self.assertEqual(self.store.production.get(first['binding']['task_id'])['plan']['observation_ref']['metadata_sha256'], 'a' * 64)
        self.assertEqual(len(self.store.production.create_calls), 3)

    def test_strict_contract_rejects_code_urls_refs_missing_scope_and_conflicts(self):
        invalid = [
            {**self.body(), 'code': 'print(1)'},
            {**self.body(), 'template_id': 'python'},
            {**self.body(), 'context': {'source_task_id': 'https://example.org/a.tif'}},
            {**self.body(), 'context': {'source_task_id': 'unit_native', 'observation_id': 'obs_unit'}},
            {**self.body(), 'parameters': {'source_task_id': {'node_id': 'elsewhere'}}},
            self.body(index_id='ndwi'), self.body(index_id='ndre'), self.body(min_patch_pixels=True),
            self.body(direction='arbitrary'), {**self.observation_body(), 'context': {'observation_id': 'obs_unit'}},
            {**self.observation_body(), 'context': {**self.observation_body()['context'], 'source_id': 'pc_landsat_c2_l2'}},
        ]
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(GeoError):
                self.service.create(body)
        self.assertEqual(self.service.list(), [])
        self.assertEqual(self.store.raster_processing.list(), [])

    def test_redundant_bbox_must_exactly_match_existing_source(self):
        body = self.body()
        body['context']['bbox'] = self.store.production.bbox
        self.assertTrue(self.service.prepare(body)['executable'])
        body['context']['bbox'] = [114., 30., 114.01, 30.01]
        with self.assertRaises(GeoError) as error:
            self.service.prepare(body)
        self.assertEqual(error.exception.code, 'WORKFLOW_SCOPE_CONFLICT')

    def test_idempotency_and_input_conflicts(self):
        body = {**self.body(), 'request_id': 'isolated-idempotent'}
        first = self.service.create(body)
        again = self.service.create(copy.deepcopy(body))
        self.assertEqual(first['id'], again['id'])
        with self.assertRaises(GeoError) as error:
            self.service.create({**body, 'parameters': {'direction': 'below'}})
        self.assertEqual(error.exception.code, 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.wait(first['id'])['status'], 'succeeded')

    def test_successful_child_reuse_is_validated_and_does_not_dispatch_twice(self):
        first = self.wait(self.service.create(self.body())['id'])
        second = self.wait(self.service.create(self.body())['id'])
        self.assertEqual(second['status'], 'succeeded', second)
        self.assertEqual(first['result_ref'], second['result_ref'])
        self.assertTrue(second['nodes'][1]['binding']['reused'])
        self.assertEqual(len(self.store.raster_processing.list()), 1)

    def test_frozen_source_and_observation_changes_block_before_dispatch(self):
        for body, mutate in (
            (self.body(), lambda: (self.store.production.output_dir / 'unit_native' / 'analysis.tif').write_bytes(b'changed')),
            (self.observation_body(), lambda: self.store.production.observation.update(metadata_sha256='c' * 64)),
        ):
            with patch.object(self.service, 'launch'):
                job = self.service.create(body)
            mutate()
            self.service.launch(job['id'])
            job = self.wait(job['id'])
            self.assertEqual(job['status'], 'failed', job)
            self.assertIn(job['error']['code'], ('WORKFLOW_SOURCE_CHANGED', 'WORKFLOW_OBSERVATION_CHANGED'))
            self.assertEqual(self.store.raster_processing.list(), [])
            self.assertEqual(self.store.production.create_calls, [])

    def test_failed_suffix_retries_frozen_child_and_reuses_successful_prefix(self):
        with patch.object(segmentation, 'run_segmentation', side_effect=GeoError('UNIT_FAILURE', 'Isolated suffix failure')):
            first = self.wait(self.service.create(self.body())['id'])
        self.assertEqual(first['status'], 'failed')
        self.assertEqual(first['nodes'][0]['status'], 'succeeded')
        with patch.object(self.store.raster_processing, 'retry', wraps=self.store.raster_processing.retry) as retry:
            second = self.wait(self.service.retry(first['id'])['id'])
        self.assertEqual(second['status'], 'succeeded', second)
        self.assertEqual(second['parent_workflow_id'], first['id'])
        self.assertEqual(second['nodes'][0]['evidence'], first['nodes'][0]['evidence'])
        retry.assert_called_once_with(first['nodes'][1]['binding']['task_id'])
        self.assertEqual(self.store.production.create_calls, [])

    def test_changed_successful_prefix_cannot_be_retried(self):
        with patch.object(segmentation, 'run_segmentation', side_effect=GeoError('UNIT_FAILURE', 'Isolated failure')):
            job = self.wait(self.service.create(self.body())['id'])
        (self.store.production.output_dir / 'unit_native' / 'overlay.png').write_bytes(b'changed')
        with self.assertRaises(GeoError) as error:
            self.service.retry(job['id'])
        self.assertEqual(error.exception.code, 'WORKFLOW_CHECKPOINT_CHANGED')

    def test_cancel_shared_child_keeps_second_workflow_alive(self):
        entered, gate, patched = self.gate_segmentation()
        with patched:
            first = self.service.create(self.body())
            self.assertTrue(entered.wait(2))
            first = self.wait_binding(first['id'])
            second = self.wait_binding(self.service.create(self.body())['id'])
            child = first['nodes'][1]['binding']['task_id']
            self.assertEqual(second['nodes'][1]['binding']['task_id'], child)
            self.assertTrue(self.service.is_child_referenced('raster_processing', child, first['id']))
            self.service.cancel(first['id'])
            self.assertEqual(self.store.raster_processing.get(child)['status'], 'running')
            gate.set()
            self.assertEqual(self.wait(second['id'])['status'], 'succeeded')
            self.assertEqual(self.service.get(first['id'])['status'], 'cancelled')

    def test_cancel_exclusive_child_and_legacy_execution_reference(self):
        entered, gate, patched = self.gate_segmentation()
        with patched:
            first = self.service.create(self.body())
            self.assertTrue(entered.wait(2))
            first = self.wait_binding(first['id'])
            binding = first['nodes'][1]['binding']
            self.store.save('execution', {'id': 'old_consumer', 'status': 'running', 'binding': binding})
            self.service.cancel(first['id'])
            self.assertEqual(self.store.raster_processing.get(binding['task_id'])['status'], 'running')
            gate.set()

    def test_cancel_exclusive_child_stops_only_owned_computation(self):
        entered, gate, patched = self.gate_segmentation()
        with patched:
            job = self.service.create(self.body())
            self.assertTrue(entered.wait(2))
            job = self.wait_binding(job['id'])
            child = job['nodes'][1]['binding']['task_id']
            self.service.cancel(job['id'])
            self.assertEqual(self.store.raster_processing.get(child)['status'], 'cancelled')
            self.assertEqual(self.store.production.get('unit_native')['status'], 'succeeded')
            gate.set()

    def test_restart_reattaches_checkpoint_without_duplicate_dispatch(self):
        entered, gate, patched = self.gate_segmentation()
        with patched:
            job = self.service.create(self.body())
            self.assertTrue(entered.wait(2))
            self.wait_binding(job['id'])
            self.service.close()
            recovered = AnalysisWorkflowServices(self.store)
            self.extra_services.append(recovered)
            self.store.analysis_workflows = recovered
            recovered.resume_pending()
            gate.set()
            self.assertEqual(self.wait(job['id'], recovered)['status'], 'succeeded')
            self.assertEqual(len(self.store.raster_processing.list()), 1)

    def test_ambiguous_dispatch_recovery_fails_instead_of_replaying(self):
        with patch.object(self.service, 'launch'):
            job = self.service.create(self.body())
        job['status'] = 'running'
        job['nodes'][0]['status'] = 'dispatching'
        self.store.save(KIND, job)
        self.service.resume_pending()
        recovered = self.service.get(job['id'])
        self.assertEqual(recovered['status'], 'failed')
        self.assertEqual(recovered['error']['code'], 'WORKFLOW_DISPATCH_INTERRUPTED')
        self.assertEqual(self.store.raster_processing.list(), [])

    def test_missing_artifact_or_result_mismatch_cannot_publish_success(self):
        (self.store.production.output_dir / 'unit_native' / 'result.json').write_text('{}')
        job = self.wait(self.service.create(self.body())['id'])
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['error']['code'], 'WORKFLOW_RESULT_MISMATCH')
        self.assertIsNone(job['result_ref'])
        self.assertEqual(self.store.raster_processing.list(), [])

    def test_missing_output_artifact_cannot_publish_success(self):
        (self.store.production.output_dir / 'unit_native' / 'overlay.png').unlink()
        job = self.wait(self.service.create(self.body())['id'])
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['error']['code'], 'ARTIFACT_MISSING')
        self.assertIsNone(job['result_ref'])
        self.assertEqual(self.store.raster_processing.list(), [])

    def test_corrupted_persisted_plan_cannot_dispatch(self):
        with patch.object(self.service, 'launch'):
            job = self.service.create(self.body())
        job['plan_snapshot']['nodes'][1]['request']['direction'] = 'below'
        self.store.save(KIND, job)
        self.service.launch(job['id'])
        job = self.wait(job['id'])
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['error']['code'], 'WORKFLOW_PLAN_CHANGED')
        self.assertEqual(self.store.raster_processing.list(), [])

    def test_pinned_production_failure_retries_same_observation_without_suffix(self):
        original = self.store.production.create

        def fail(body):
            task = original(body)
            task.update(status='failed', result=None)
            self.store.save('production_task', task)
            return task

        with patch.object(self.store.production, 'create', side_effect=fail):
            job = self.wait(self.service.create(self.observation_body())['id'])
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(self.store.raster_processing.list(), [])
        retry = self.wait(self.service.retry(job['id'])['id'])
        self.assertEqual(retry['status'], 'succeeded', retry)
        self.assertEqual(self.store.production.create_calls[0], self.store.production.create_calls[1])


class AnalysisWorkflowSqliteCheckpointTest(unittest.TestCase):
    def test_real_workspace_sqlite_checkpoint_survives_full_service_restart(self):
        # All services and SQLite records are isolated under this temporary root.
        from backend.server import Workspace
        with tempfile.TemporaryDirectory(prefix='analysis-chain-sqlite-') as temporary:
            root = Path(temporary)
            workspace = Workspace(root / 'workspace.sqlite3')
            service = getattr(workspace, 'analysis_workflows', None) or AnalysisWorkflowServices(workspace)
            workspace.analysis_workflows = service
            seed_native(workspace, root, 'unit_persistent')
            entered, gate = threading.Event(), threading.Event()
            original = segmentation.run_segmentation

            def run(*args, **kwargs):
                entered.set()
                gate.wait(3)
                return original(*args, **kwargs)

            try:
                with patch.object(segmentation, 'run_segmentation', side_effect=run):
                    job = service.create({'template_id': 'spectral_partition', 'context': {'source_task_id': 'unit_persistent'}})
                    self.assertTrue(entered.wait(2))
                    service.close()
                    workspace.close()
                    gate.set()
                # Wait until the old cancelled/stopping worker is done touching
                # its isolated files before reconstructing all services.
                for thread in workspace.raster_processing.threads:
                    thread.join(2)
                restored = Workspace(root / 'workspace.sqlite3')
                restored_service = getattr(restored, 'analysis_workflows', None) or AnalysisWorkflowServices(restored)
                restored.analysis_workflows = restored_service
                try:
                    restored_service.resume_pending()
                    for _ in range(400):
                        checkpoint = restored_service.get(job['id'])
                        if checkpoint['status'] not in ('queued', 'running'):
                            break
                        time.sleep(.01)
                    self.assertEqual(checkpoint['status'], 'failed', checkpoint)
                    self.assertEqual(checkpoint['nodes'][0]['status'], 'succeeded')
                    retry = restored_service.retry(job['id'])
                    for _ in range(400):
                        retry = restored_service.get(retry['id'])
                        if retry['status'] not in ('queued', 'running'):
                            break
                        time.sleep(.01)
                    self.assertEqual(retry['status'], 'succeeded', retry)
                    self.assertEqual(retry['nodes'][0]['evidence'], checkpoint['nodes'][0]['evidence'])
                finally:
                    restored_service.close()
                    restored.close()
            finally:
                gate.set()
                service.close()
                workspace.close()


if __name__ == '__main__':
    unittest.main()
