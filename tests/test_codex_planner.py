"""Isolated language-planner contracts. No network, CLI inference or business DB."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.integrations.codex_gateway import CodexGatewayError
from backend.integrations.codex_planner import CodexPlanner
from backend.core.geo_services import GeoError


def output(decision='reply', **extra):
    return {'decision': decision, 'title': '隔离测试', 'message': '植被指数反映光谱差异。',
            'application_id': '', 'parameters_json': '', 'context_json': '', 'product_id': '', **extra}


class Gateway:
    def __init__(self, value=None, error=None): self.value = value or output(); self.error = error; self.calls = []
    def complete(self, payload, schema, cancel=None):
        self.calls.append(copy.deepcopy(payload))
        if self.error: raise self.error
        return {'output': copy.deepcopy(self.value), 'metadata': {'provider': 'codex_cli', 'model': 'isolated'}}


class Applications:
    def __init__(self): self.calls = []
    def catalog(self):
        return {'applications': [
            {'id': 'cropland_delineation', 'name': '隔离耕作地', 'ready': True,
             'form': {'requires_bbox': True, 'period_mode': 'none', 'fields': [{'key': 'product'}]},
             'defaults': {'parameters': {'product': 'io_lulc_2024'}}, 'method': {}},
            {'id': 'vegetation_status', 'name': '隔离光谱', 'ready': True,
             'form': {'requires_bbox': True, 'period_mode': 'single', 'fields': [{'key': 'source_id'}, {'key': 'threshold'}]},
             'defaults': {'parameters': {}}, 'method': {'index_id': 'ndvi'}},
            {'id': 'vegetation_cover', 'name': '隔离覆盖度', 'ready': True,
             'form': {'requires_bbox': False, 'period_mode': 'none', 'fields': [{'key': 'soil_ndvi'}, {'key': 'vegetation_ndvi'}, {'key': 'source_ref'}]},
             'defaults': {'parameters': {}}, 'method': {'agriculture_algorithm_id': 'fvc'}},
            {'id': 'imagery_search', 'name': '隔离目录', 'ready': True,
             'form': {'requires_bbox': True, 'period_mode': 'single', 'fields': [{'key': 'source_id'}]},
             'defaults': {'parameters': {}}, 'method': {}},
        ]}
    def prepare(self, body):
        self.calls.append(copy.deepcopy(body))
        app = next(row for row in self.catalog()['applications'] if row['id'] == body['application_id'])
        if set(body['parameters']) - {row['key'] for row in app['form']['fields']}:
            raise GeoError('UNKNOWN_PARAMETERS', 'unknown parameters')
        frozen = {'id': app['id'], 'name': app['name'], 'parameters': body['parameters'], 'context': body['context']}
        return {'question': '可信专题生成的问题', 'context': body['context']}, frozen


class Executions:
    def __init__(self, result=None): self.result = result; self.calls = []
    def prepare(self, question, context):
        self.calls.append((question, copy.deepcopy(context)))
        return copy.deepcopy(self.result or {'intent': None, 'status': 'needs_data', 'blocking_gaps': ['unknown language'], 'tool': None})


class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.apps = Applications(); self.executions = Executions()
        self.history = {}
        self.store = SimpleNamespace(applications=self.apps, executions=self.executions,
                                     assistant=SimpleNamespace(get=lambda ident: self.history[ident]))
        self.q = {'id': 'isolated-question', 'question': 'NDVI是什么意思？', 'context': {}}
    def plan(self, result, q=None):
        gateway = Gateway(result)
        return CodexPlanner(self.store, gateway).plan(q or self.q), gateway
    def app_output(self, app='cropland_delineation', parameters=None, context=None):
        return output('application', application_id=app, parameters_json=json.dumps(parameters or {'product': 'io_lulc_2024'}), context_json=json.dumps(context or {}))

    def test_general_answer_does_not_prepare_or_claim_actual_execution(self):
        result, gateway = self.plan(output())
        self.assertEqual(result['kind'], 'reply'); self.assertIsNone(result['execution_question'])
        self.assertEqual(self.executions.calls, []); self.assertEqual(self.apps.calls, [])
        self.assertEqual(result['provenance']['planner_mode'], 'codex_cli')
        self.assertEqual(result['provenance']['provider'], 'codex_cli')
        self.assertEqual(result['provenance']['model'], 'isolated')
        self.assertIsInstance(result['provenance']['metadata'], dict)
        self.assertIn('applications', gateway.calls[0])

    def test_quantified_business_request_cannot_be_answered_by_model_text(self):
        q = {**self.q, 'question': '能否告诉我选区农田有多少亩？'}
        result, _ = self.plan(output(message='面积是999亩。'), q)
        self.assertEqual(result['kind'], 'fallback'); self.assertEqual(self.executions.calls, [])
        q['question'] = '介绍仙桃市的农业情况'
        result, _ = self.plan(output(message='耕地999亩。'), q)
        self.assertEqual(result['kind'], 'fallback')
        q['question'] = '请解释仙桃今年农田面积是多少，说明依据'
        result, _ = self.plan(output(message='目前没有权威调查。'), q)
        self.assertEqual(result['kind'], 'fallback')

    def test_model_clarification_has_no_planning_side_effects(self):
        result, _ = self.plan(output('clarify', message='请说明日期。'))
        self.assertEqual(result['kind'], 'clarify'); self.assertEqual(self.executions.calls, [])

    def test_natural_rephrasing_maps_to_registered_application_and_keeps_scope(self):
        bbox = [113.3, 30.3, 113.31, 30.31]
        q = {**self.q, 'question': '这块地种庄稼的地方都描出来', 'context': {'bbox': bbox, 'cropland_product': 'io_lulc_2024'}}
        before = copy.deepcopy(q)
        result, _ = self.plan(self.app_output(), q)
        self.assertEqual(result['kind'], 'execute'); self.assertEqual(result['execution_context']['bbox'], bbox)
        self.assertEqual(result['application_ref']['id'], 'cropland_delineation')
        self.assertEqual(result['execution_question'], '可信专题生成的问题'); self.assertEqual(q, before)
        self.assertEqual(result['provenance']['original_question'], q['question'])

    def test_model_cannot_fabricate_or_replace_map_scope(self):
        for trusted in ({}, {'bbox': [113.3, 30.3, 113.31, 30.31]}):
            q = {**self.q, 'question': '标出种庄稼的位置', 'context': trusted}
            result, _ = self.plan(self.app_output(context={'bbox': [1, 2, 3, 4]}), q)
            self.assertEqual(result['kind'], 'clarify'); self.assertEqual(self.apps.calls, [])

    def test_model_cannot_replace_dataset_observation_or_add_unknown_context(self):
        for key in ('dataset_id', 'observation_id'):
            result, _ = self.plan(self.app_output(context={key: 'invented'}))
            self.assertEqual(result['kind'], 'clarify')
        result, _ = self.plan(self.app_output(context={'url': 'https://example.invalid'}))
        self.assertEqual(result['kind'], 'fallback')

    def test_explicit_year_source_and_product_are_not_replaced(self):
        bbox = [113.3, 30.3, 113.31, 30.31]
        q = {**self.q, 'question': '标注选区2021年耕作地', 'context': {'bbox': bbox}}
        result, _ = self.plan(self.app_output(), q); self.assertEqual(result['kind'], 'fallback')
        q = {**q, 'question': '用Landsat查看2025年10月NDVI'}
        result, _ = self.plan(self.app_output('vegetation_status', {'source_id': 'pc_sentinel2_l2a'}, {'period': {'start': '2025-10-01', 'end': '2025-10-31'}}), q)
        self.assertEqual(result['kind'], 'fallback')

    def test_missing_or_invented_dates_never_use_catalog_defaults(self):
        q = {**self.q, 'question': '看看这块地庄稼绿不绿', 'context': {'bbox': [113.3, 30.3, 113.31, 30.31]}}
        proposed = {'period': {'start': '2025-10-01', 'end': '2025-10-31'}}
        for context in ({}, proposed):
            result, _ = self.plan(self.app_output('vegetation_status', {'source_id': 'auto'}, context), q)
            self.assertEqual(result['kind'], 'clarify')
        q['question'] = '看看2026年10月选区NDVI'
        result, _ = self.plan(self.app_output('vegetation_status', {'source_id': 'auto'}, proposed), q)
        self.assertEqual(result['kind'], 'clarify')

    def test_correct_month_is_normalized_and_optional_threshold_is_not_invented(self):
        q = {**self.q, 'question': '看看2025年10月选区NDVI', 'context': {'bbox': [113.3, 30.3, 113.31, 30.31]}}
        proposed = {'period': {'start': '2025-10-01', 'end': '2025-10-31'}}
        result, _ = self.plan(self.app_output('vegetation_status', {'source_id': 'auto'}, proposed), q)
        self.assertEqual(result['kind'], 'execute')
        self.assertIsNone(self.apps.calls[-1]['parameters']['threshold'])

    def test_ui_date_cannot_override_an_explicit_conflicting_year(self):
        q = {**self.q, 'question': '看看2025年10月选区NDVI', 'context': {'bbox': [113.3, 30.3, 113.31, 30.31], 'period': {'start': '2026-10-01', 'end': '2026-10-31'}}}
        result, _ = self.plan(self.app_output('vegetation_status', {'source_id': 'auto'}), q)
        self.assertEqual(result['kind'], 'clarify')

    def test_fvc_missing_endmembers_or_unselected_source_is_clarified(self):
        q = {**self.q, 'question': '估算FVC植被覆盖度'}
        result, _ = self.plan(self.app_output('vegetation_cover', {'source_ref': 'production:hidden'}), q)
        self.assertEqual(result['kind'], 'clarify')
        self.assertIn('soil_ndvi', result['clarification']['missing_fields'])
        self.assertIn('source_ref', result['clarification']['missing_fields'])

    def test_original_unsupported_and_regional_conflicts_remain_on_legacy_path(self):
        for preflight in ({'status': 'unsupported', 'blocking_gaps': ['official scope']},
                          {'status': 'needs_data', 'intent': 'regional_product_read', 'blocking_gaps': ['wrong month']}):
            self.executions.result = preflight
            result, _ = self.plan(self.app_output())
            self.assertEqual(result['kind'], 'fallback'); self.assertEqual(self.apps.calls, [])

    def test_frozen_history_and_source_references_never_go_to_model(self):
        self.history['old'] = {'binding': {'task_id': 'actual'}, 'answer': {'result_ref': {'task_id': 'actual'}}}
        for context in ({'reference_question_id': 'old'}, {'reference_execution_id': 'fixed'}, {'source_type': 'agriculture_atlas', 'source_task_id': 'fixed'}, {'atlas_product_sha256': 'a' * 64}):
            result, gateway = self.plan(output(), {**self.q, 'context': context})
            self.assertEqual(result['kind'], 'fallback'); self.assertEqual(gateway.calls, [])

    def test_general_conversation_history_is_bounded_and_not_treated_as_execution(self):
        for i in range(6):
            self.history[str(i)] = {'question': '解释NDVI' + str(i), 'status': 'answered', 'answer': {'summary': '一般知识'}, 'parent_question_id': str(i - 1) if i else None}
        result, gateway = self.plan(output(), {**self.q, 'parent_question_id': '5', 'context': {'reference_question_id': '5'}})
        self.assertEqual(result['kind'], 'reply'); self.assertEqual(len(gateway.calls[0]['history']), 4)

    def test_malformed_inner_json_duplicate_keys_nan_and_unknown_parameters_rejected(self):
        for text in ('{"product":"io_lulc_2024","product":"worldcover_2021_v200"}', '{"x":NaN}', '[]', '{"__exec":"shell"}'):
            result, _ = self.plan(self.app_output(parameters={}) | {'parameters_json': text})
            self.assertEqual(result['kind'], 'fallback')

    def test_gateway_errors_propagate_for_root_cancellation_and_retry_handling(self):
        gateway = Gateway(error=CodexGatewayError('CODEX_TIMEOUT', 'private stderr sk-do-not-display'))
        with self.assertRaises(CodexGatewayError) as error:
            CodexPlanner(self.store, gateway).plan(self.q)
        self.assertEqual(error.exception.code, 'CODEX_TIMEOUT')

    def test_imagery_search_preserves_explicit_s2_s1_and_landsat_enums(self):
        for source, name in [('sentinel2', 'Sentinel-2'), ('sentinel1', 'Sentinel-1'), ('landsat', 'Landsat')]:
            q = {**self.q, 'question': '找' + name + '选区2025年10月影像', 'context': {'bbox': [113.3, 30.3, 113.31, 30.31]}}
            period = {'period': {'start': '2025-10-01', 'end': '2025-10-31'}}
            result, _ = self.plan(self.app_output('imagery_search', {'source_id': source}, period), q)
            self.assertEqual(result['kind'], 'execute', source)
            result, _ = self.plan(self.app_output('imagery_search', {'source_id': 'all'}, period), q)
            self.assertEqual(result['kind'], 'fallback', source)

    def test_regional_selected_product_cannot_disagree_with_original_archive_route(self):
        self.executions.result = {'status': 'ready', 'intent': 'regional_product_read', 'executable': True, 'tool': {'id': 'regional_product_read'}}
        plan = {'executable': True, 'tool': {'id': 'regional_product_read', 'inputs': {'product_id': 'original-product'}}}
        with patch('backend.integrations.codex_planner.prepare_regional', return_value=plan):
            result, _ = self.plan(output('regional_read', product_id='other-product'))
            self.assertEqual(result['kind'], 'fallback')
            result, _ = self.plan(output('regional_read', product_id='original-product'))
            self.assertEqual(result['kind'], 'execute'); self.assertEqual(result['execution_question'], self.q['question'])


if __name__ == '__main__': unittest.main()
