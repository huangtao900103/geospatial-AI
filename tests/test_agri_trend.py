"""Isolated scalar trend contracts: no network, real imagery, or production DB."""
import copy
import hashlib
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from backend.agriculture.agri_research_services import AgriResearchServices, digest
from backend.agriculture.agri_trend_services import (AgriTrendServices, KIND, VERSION,
    prepare_trend_question, trend_answer)
from backend.core.geo_services import GeoError


class IsolatedStore:
    def __init__(self, root):
        self.db_path = str(Path(root) / 'isolated-test.sqlite3')
        self.rows = {}

    def save(self, kind, row):
        self.rows.setdefault(kind, {})[row['id']] = copy.deepcopy(row)
        return copy.deepcopy(row)

    def get(self, kind, ident):
        if ident not in self.rows.get(kind, {}):
            raise GeoError('TEST_NOT_FOUND', 'isolated record absent', 404)
        return copy.deepcopy(self.rows[kind][ident])

    def list(self, kind):
        return copy.deepcopy(list(self.rows.get(kind, {}).values()))


class AgriTrendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = IsolatedStore(self.tmp.name)
        self.store.agri_research = AgriResearchServices(self.store)
        self.service = self.store.agri_trend = AgriTrendServices(self.store)
        self.raw = self.fixture()
        self.write(self.raw)

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, ident='isolated-trend-2026', days=None, values=None):
        days = days or ['2026-05-01', '2026-05-06', '2026-05-21']
        values = values or [.1, .15, .3]
        root = self.store.agri_research.root / ident
        root.mkdir(parents=True, exist_ok=True)
        observations, artifacts = [], []
        for i, (day, value) in enumerate(zip(days, values)):
            # These text files are hash fixtures, deliberately not remote/native imagery.
            name = 'isolated-observation-%d.txt' % i
            payload = ('unit-test evidence only, not actual pixels: ' + day).encode()
            (root / name).write_bytes(payload)
            sha = hashlib.sha256(payload).hexdigest()
            artifacts.append({'file': name, 'sha256': sha})
            observations.append({'date': day, 'datetime': day + 'T10:00:00Z',
                'scene_id': 'isolated-scene-' + str(i), 'platform': 'landsat-8',
                'native_file': name, 'sha256': sha, 'comparison_eligible': True,
                'ndvi_mean': .9 - i * .3, 'ndmi_mean': .6,
                'ndvi_valid_pixel_count': 100, 'ndmi_valid_pixel_count': 100,
                'qa_valid_ratio': .9, 'ndvi_valid_ratio': .9, 'ndmi_valid_ratio': .9,
                'quality_common_mean_ndvi': value, 'quality_common_mean_ndmi': .4 - value * 2})
        support = b'unit-test fixed support, not a raster'
        (root / 'isolated-common-support.txt').write_bytes(support)
        support_sha = hashlib.sha256(support).hexdigest()
        artifacts.append({'file': 'isolated-common-support.txt', 'sha256': support_sha})
        return {'id': ident, 'status': 'ready', 'title': 'isolated actual-date contract fixture',
            'platform': 'landsat-8', 'source_id': 'pc_landsat_c2_l2',
            'scope': {'region': '仙桃市', 'boundary_year': 2017},
            'period': {'start': '2026-05-01', 'end': '2026-05-31'},
            'grid': {'crs': 'EPSG:32649', 'resolution_m': 30, 'cell_area_m2': 900,
                     'shape': [10, 10], 'transform': [30, 0, 684000, 0, -30, 3379800]},
            'observations': observations, 'artifacts': artifacts,
            'sequence_summary': {'common_support_pixels': 80, 'missing_windows': ['2026-05-11-20']},
            'quality_comparison': {'status': 'ready', 'common_support_pixels': 80,
                'common_support_ratio': .8, 'common_area_ha_30m': 7.2,
                'sha256': support_sha, 'native_file': 'isolated-common-support.txt',
                'dates': list(days), 'source_sha256s': [{'date': row['date'], 'sha256': row['sha256']} for row in observations],
                'excluded_dates_with_reasons': [], 'rule': {'qa_valid_ratio_minimum': .7,
                    'ndvi_valid_ratio_minimum': .7, 'ndmi_valid_ratio_minimum': .7,
                    'same_spatial_mask_for_both_indices': True, 'minimum_eligible_dates': 2}},
            'limitations': ['Isolated test evidence; not actual agricultural observations.']}

    def write(self, raw=None):
        raw = self.raw if raw is None else raw
        root = self.store.agri_research.root / raw['id']
        (root / 'result.json').write_text(json.dumps(raw, allow_nan=False), encoding='utf-8')

    def body(self, index='ndvi', raw=None):
        raw = self.raw if raw is None else raw
        item = self.store.agri_research.get(raw['id'])
        return {'series_id': item['id'], 'series_sha256': item['series_sha256'], 'index_id': index}

    def assertError(self, code, fn):
        with self.assertRaises(GeoError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_actual_unequal_days_and_common_means_drive_sen_not_sequence_order(self):
        before = (self.store.agri_research.root / self.raw['id'] / 'result.json').read_bytes()
        row = self.service.create(self.body())
        summary = row['result']['summary']
        self.assertEqual(row['status'], 'succeeded')
        self.assertAlmostEqual(summary['slope_per_day'], .01)
        self.assertAlmostEqual(summary['change_per_30_days'], .3)
        self.assertEqual(summary['span_days'], 20)
        self.assertEqual(summary['max_gap_days'], 15)
        self.assertEqual(summary['pair_count'], 3)
        self.assertEqual(summary['actual_dates'], ['2026-05-01', '2026-05-06', '2026-05-21'])
        self.assertEqual(row['result']['provenance']['algorithm_version'], VERSION)
        self.assertEqual(before, (self.store.agri_research.root / self.raw['id'] / 'result.json').read_bytes())
        self.assertEqual(len(self.store.list(KIND)), 1)

    def test_ndmi_uses_ndmi_common_values_and_has_no_ndvi_map_binding(self):
        row = self.service.create(self.body('ndmi'))
        self.assertAlmostEqual(row['result']['summary']['slope_per_day'], -.02)
        answer = trend_answer(row)
        self.assertEqual(answer['map_effect'], 'none')
        self.assertIsNone(answer['result_ref'])
        self.assertEqual(answer['research_series']['observations'][0]['mean_common_support'], .2)
        self.assertEqual(answer['provenance']['input_values'], 'quality_common_mean_ndmi')
        self.assertNotIn('product_id', answer['provenance'])

    def test_zero_is_a_valid_observation_and_constant_trend_is_zero(self):
        for item in self.raw['observations']:
            item['quality_common_mean_ndvi'] = 0
        self.write()
        summary = self.service.create(self.body())['result']['summary']
        self.assertEqual(summary['slope_per_day'], 0)
        self.assertEqual(summary['change_per_30_days'], 0)
        self.assertEqual(summary['trend_direction'], 'unchanged')

    def test_pairwise_median_is_robust_to_an_isolated_extreme(self):
        days = [(date(2026, 5, 1) + timedelta(days=i)).isoformat() for i in range(7)]
        self.raw = self.fixture(days=days, values=[0, .1, .2, .95, .4, .5, .6])
        self.write()
        summary = self.service.create(self.body())['result']['summary']
        self.assertAlmostEqual(summary['slope_per_day'], .1)
        self.assertEqual(summary['pair_count'], 21)

    def test_low_quality_date_is_excluded_without_interpolation_or_zero_fill(self):
        item = copy.deepcopy(self.raw['observations'][1])
        item.update(date='2026-05-10', datetime='2026-05-10T10:00:00Z', scene_id='isolated-cloudy',
                    comparison_eligible=False, quality_common_mean_ndvi=None, quality_common_mean_ndmi=None,
                    qa_valid_ratio=.2, ndvi_valid_ratio=.2, ndmi_valid_ratio=.2)
        self.raw['observations'].insert(2, item)
        comparison = self.raw['quality_comparison']
        comparison['source_sha256s'] = [{'date': row['date'], 'sha256': row['sha256']} for row in self.raw['observations']]
        comparison['excluded_dates_with_reasons'] = [{'date': item['date'],
            'reasons': [{'field': 'qa_valid_ratio', 'value': .2, 'minimum': .7}]}]
        self.write()
        result = self.service.create(self.body())['result']
        self.assertAlmostEqual(result['summary']['slope_per_day'], .01)
        self.assertEqual(result['summary']['observation_count'], 3)
        self.assertEqual(result['summary']['excluded_observation_count'], 1)
        self.assertIsNone(result['time_series'][2]['mean_common_support'])
        self.assertEqual(result['excluded_observations'][0]['date'], '2026-05-10')
        self.assertEqual(result['missing_windows'], ['2026-05-11-20'])
        self.assertFalse(result['provenance']['interpolation'])
        self.assertFalse(result['provenance']['missing_value_imputation'])

    def test_duplicate_dates_are_rejected_not_merged(self):
        self.raw['observations'][1]['date'] = '2026-05-01'
        self.write()
        self.assertError('TREND_DUPLICATE_DATE', lambda: self.service.create(self.body()))
        self.assertEqual(self.store.list(KIND), [])

    def test_unordered_or_invalid_dates_are_rejected(self):
        self.raw['observations'].reverse()
        self.write()
        self.assertError('TREND_INVALID_DATE', lambda: self.service.prepare(self.body()))

    def test_utc_timestamp_cannot_disagree_with_actual_observation_day(self):
        self.raw['observations'][0]['datetime'] = '2026-05-02T10:00:00Z'
        self.write()
        self.assertError('TREND_INVALID_DATE', lambda: self.service.prepare(self.body()))
        self.raw['observations'][0]['date'] = '2026-5-1'
        self.write()
        self.assertError('TREND_INVALID_DATE', lambda: self.service.prepare(self.body()))

    def test_no_common_support_is_rejected(self):
        self.raw['quality_comparison'].update(status='insufficient_common_support', common_support_pixels=0)
        self.write()
        self.assertError('TREND_NO_COMMON_SUPPORT', lambda: self.service.create(self.body()))
        self.assertEqual(self.store.list(KIND), [])

    def test_no_quality_comparison_cannot_fall_back_to_own_or_legacy_means(self):
        del self.raw['quality_comparison']
        self.write()
        self.assertError('TREND_COMMON_SUPPORT_REQUIRED', lambda: self.service.create(self.body()))

    def test_two_dates_are_not_a_supported_trend(self):
        self.raw['observations'] = self.raw['observations'][:2]
        self.raw['quality_comparison']['dates'] = self.raw['quality_comparison']['dates'][:2]
        self.raw['quality_comparison']['source_sha256s'] = self.raw['quality_comparison']['source_sha256s'][:2]
        self.write()
        self.assertError('TREND_INSUFFICIENT_DATES', lambda: self.service.create(self.body()))

    def test_missing_common_mean_never_falls_back_to_own_mean(self):
        self.raw['observations'][1]['quality_common_mean_ndvi'] = None
        self.write()
        self.assertError('TREND_COMMON_MEAN_REQUIRED', lambda: self.service.create(self.body()))

    def test_quality_flag_and_source_hash_mismatch_are_rejected(self):
        self.raw['observations'][0]['qa_valid_ratio'] = .2
        self.write()
        self.assertError('TREND_SOURCE_INVALID', lambda: self.service.prepare(self.body()))
        self.raw['observations'][0]['qa_valid_ratio'] = .9
        self.raw['quality_comparison']['source_sha256s'][0]['sha256'] = 'f' * 64
        self.write()
        self.assertError('TREND_SOURCE_HASH_MISMATCH', lambda: self.service.prepare(self.body()))

    def test_sensor_and_explicit_grid_mismatch_are_rejected(self):
        self.raw['observations'][0]['platform'] = 'landsat-9'
        self.write()
        self.assertError('TREND_SENSOR_MISMATCH', lambda: self.service.prepare(self.body()))
        self.raw['observations'][0]['platform'] = 'landsat-8'
        self.raw['observations'][0]['grid'] = {**self.raw['grid'], 'crs': 'EPSG:32650'}
        self.write()
        self.assertError('TREND_GRID_MISMATCH', lambda: self.service.prepare(self.body()))

    def test_stale_source_sha_and_bad_request_are_rejected_without_record(self):
        body = self.body()
        self.raw['observations'][0]['quality_common_mean_ndvi'] = .2
        self.write()
        self.assertEqual(self.assertError('SERIES_VERSION_CHANGED', lambda: self.service.create(body)).status, 409)
        for body in ({}, {**self.body(), 'bbox': [113, 30, 114, 31]},
                     {**self.body(), 'index_id': 'evi'}, {**self.body(), 'series_sha256': 'bad'}):
            self.assertError('INVALID_TREND_INPUT', lambda body=body: self.service.prepare(body))
        self.assertEqual(self.store.list(KIND), [])

    def test_source_mutation_between_prepare_and_commit_cannot_save(self):
        prepare = self.service.prepare
        calls = []
        def changing(body):
            plan = prepare(body)
            calls.append(True)
            if len(calls) == 1:
                self.raw['observations'][0]['quality_common_mean_ndvi'] = .25
                self.write()
            return plan
        self.service.prepare = changing
        self.assertError('SERIES_VERSION_CHANGED', lambda: self.service.create(self.body()))
        self.assertEqual(self.store.list(KIND), [])

    def test_derived_record_has_own_immutable_digest_and_download_binding(self):
        row = self.service.create(self.body())
        frozen = {key: value for key, value in row.items() if key != 'record_sha256'}
        self.assertEqual(row['record_sha256'], digest(frozen))
        self.assertEqual(self.service.get(row['id'], row['record_sha256']), row)
        self.assertEqual(self.service.list(), [row])
        answer = trend_answer(row)
        self.assertEqual(answer['trend_ref'], {'id': row['id'], 'url': '/api/agri-trends/' + row['id'],
                                               'record_sha256': row['record_sha256']})
        self.assertEqual(answer['downloads'][0]['url'], answer['trend_ref']['url'])
        self.assertEqual(answer['provenance']['series_sha256'], self.body()['series_sha256'])
        self.assertEqual(answer['trend_diagnostics']['significance_status'], 'not_assessed')
        self.assertIsNone(answer['trend_diagnostics']['p_value'])
        self.assertFalse(answer['trend_diagnostics']['annual_trend_assessed'])
        self.assertFalse(answer['trend_diagnostics']['crop_filtered'])
        self.assertFalse(answer['trend_diagnostics']['forecast'])
        self.assertError('TREND_RESULT_CHANGED', lambda: self.service.get(row['id'], 'f' * 64))
        self.store.rows[KIND][row['id']]['plan']['index_id'] = 'ndmi'
        self.assertError('TREND_RESULT_CHANGED', lambda: self.service.get(row['id']))

    def test_non_trend_and_general_knowledge_questions_do_not_match(self):
        for question in ('查看仙桃时序', '什么是Sen时序趋势', '介绍Mann-Kendall时序趋势的原理', '分析仙桃NDVI趋势'):
            self.assertIsNone(prepare_trend_question(self.store, question, {}))

    def test_question_routes_only_unique_matching_archive_with_exact_constraints(self):
        for question, context, index in (
            ('分析仙桃全市2026年NDVI时序趋势', {}, 'ndvi'),
            ('分析仙桃全市2026年NDVI时序趋势并给出公式', {}, 'ndvi'),
            ('分析全市现有归档NDMI时序斜率', {'region': '仙桃市'}, 'ndmi'),
            ('计算仙桃2026-05-01至2026-05-31观测序列Sen斜率', {}, 'ndvi')):
            plan = prepare_trend_question(self.store, question, context)
            self.assertTrue(plan['executable'], plan['message'])
            self.assertEqual(plan['inputs'], self.body(index))
        other = self.fixture('isolated-other-trend-2026')
        self.write(other)
        plan = prepare_trend_question(self.store, '分析仙桃全市NDVI时序趋势', {})
        self.assertFalse(plan['executable'])
        self.assertIn('多个', plan['message'])
        plan = prepare_trend_question(self.store, '分析仙桃全市NDVI时序趋势', {
            'source_type': 'agri_research_series', 'source_task_id': self.raw['id']})
        self.assertTrue(plan['executable'])
        self.assertEqual(plan['inputs']['series_id'], self.raw['id'])

    def test_question_keeps_scope_time_source_and_agriculture_limits(self):
        cases = [
            ('分析仙桃市农田时序趋势', {}),
            ('分析仙桃水稻产量时序趋势', {}),
            ('分析仙桃全市全年时序趋势', {}),
            ('分析仙桃全市年际时序趋势', {}),
            ('分析仙桃全市显著时序趋势', {}),
            ('分析仙桃所选区域时序趋势', {'bbox': [113, 30, 113.1, 30.1]}),
            ('分析仙桃全市2025年时序趋势', {}),
            ('分析仙桃2026-05-01至2026-05-20时序趋势', {}),
            ('分析仙桃全市Sentinel-2时序趋势', {}),
            ('分析仙桃全市Landsat9时序趋势', {}),
            ('分析武汉全市NDVI时序趋势', {'region': '仙桃市'}),
            ('分析仙桃全市NDVI和NDMI时序趋势', {}),
            ('分析仙桃全市NDMI时序趋势', {'index_id': 'ndvi'}),
            ('分析仙桃全市NDVI时序趋势', {'index_id': 'ndmi'}),
            ('分析仙桃全市NDVI时序趋势', {'source_type': 'agriculture_atlas', 'source_task_id': 'a'}),
            ('分析仙桃全市NDVI时序趋势', {'dataset_id': 'user-field'}),
            ('分析仙桃全市NDVI时序趋势', {'source_task_id': self.raw['id']}),
            ('分析仙桃全市NDVI时序趋势', {'series_sha256': 'f' * 64}),
            ('分析仙桃全市当前NDVI时序趋势', {}),
        ]
        for question, context in cases:
            with self.subTest(question=question, context=context):
                plan = prepare_trend_question(self.store, question, context)
                self.assertIsNotNone(plan)
                self.assertFalse(plan['executable'], question)
                self.assertEqual(plan['status'], 'needs_data')
                self.assertIsNone(plan['inputs'])
        self.assertEqual(self.store.list(KIND), [])

    def test_isolated_workspace_question_direct_api_and_report_download_roundtrip(self):
        """Actual handlers on an ephemeral port, using only this temp fixture."""
        import threading
        import time
        from unittest.mock import patch
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen
        from backend.server import LocalServer, Workspace, make_handler

        before = (self.store.agri_research.root / self.raw['id'] / 'result.json').read_bytes()
        workspace = Workspace(self.store.db_path)
        server = LocalServer(('127.0.0.1', 0), make_handler(workspace))
        server.quiet = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)

        def request(path, body=None):
            data = json.dumps(body, allow_nan=False).encode() if body is not None else None
            call = Request(base + path, data=data, headers={'Content-Type': 'application/json', 'Origin': base})
            try:
                response = urlopen(call, timeout=10)
            except HTTPError as error:
                response = error
            with response:
                return response.status, json.loads(response.read())

        try:
            self.assertNotEqual(server.server_port, 8016)
            self.assertEqual(workspace.agri_research.root, self.store.agri_research.root)
            workspace.model_gateway.enabled = True
            with patch.object(workspace.model_planner, 'plan', side_effect=AssertionError('archive trend must use bounded tool')):
                status, question = request('/api/assistant/questions', {
                    'question': '分析仙桃已有归档NDVI时序的稳健趋势', 'context': {}})
                self.assertEqual(status, 201)
                for _ in range(400):
                    status, question = request('/api/assistant/questions/' + question['id'])
                    self.assertEqual(status, 200)
                    if question['status'] != 'processing':
                        break
                    time.sleep(.01)
            self.assertEqual(question['status'], 'answered', question)
            answer = question['answer']
            self.assertEqual(question['capability_id'], 'agri_series_trend')
            self.assertAlmostEqual(answer['trend_diagnostics']['slope_per_day'], .01)
            self.assertEqual(answer['map_effect'], 'none')
            self.assertIsNone(answer['result_ref'])
            self.assertEqual(answer['provenance']['series_sha256'], self.body()['series_sha256'])
            self.assertEqual(answer['trend_diagnostics']['significance_status'], 'not_assessed')
            self.assertEqual(answer['downloads'][0]['url'], answer['trend_ref']['url'])
            self.assertEqual(question['answer_snapshot'], answer)
            status, downloaded = request(answer['trend_ref']['url'])
            self.assertEqual(status, 200)
            self.assertEqual(downloaded['id'], answer['trend_ref']['id'])
            self.assertEqual(downloaded['record_sha256'], answer['trend_ref']['record_sha256'])
            self.assertEqual(downloaded['record_sha256'], digest({
                key: value for key, value in downloaded.items() if key != 'record_sha256'}))
            self.assertAlmostEqual(downloaded['result']['summary']['change_per_30_days'], .3)

            body = self.body('ndmi')
            status, plan = request('/api/agri-trends/prepare', body)
            self.assertEqual(status, 200)
            self.assertEqual(plan['request'], body)
            status, direct = request('/api/agri-trends', body)
            self.assertEqual(status, 201)
            self.assertAlmostEqual(direct['result']['summary']['slope_per_day'], -.02)
            status, report = request(direct['result']['downloads'][0]['url'])
            self.assertEqual(status, 200)
            self.assertEqual(report, direct)
            self.assertEqual(report['record_sha256'], digest({
                key: value for key, value in report.items() if key != 'record_sha256'}))
            status, records = request('/api/agri-trends')
            self.assertEqual(status, 200)
            self.assertEqual({row['id'] for row in records}, {downloaded['id'], direct['id']})
            status, error = request('/api/agri-trends', {**body, 'series_sha256': 'f' * 64})
            self.assertEqual(status, 409)
            self.assertEqual(error['error']['code'], 'SERIES_VERSION_CHANGED')
            self.assertEqual(len(workspace.agri_trend.list()), 2)
            self.assertEqual(before, (workspace.agri_research.root / self.raw['id'] / 'result.json').read_bytes())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
            workspace.close()


if __name__ == '__main__':
    unittest.main()
