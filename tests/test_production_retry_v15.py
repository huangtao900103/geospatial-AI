"""Retry must keep the frozen provider, scene, and observation version."""
import copy
import types
import unittest
from unittest.mock import Mock

from geo_services import GeoError
from production_services import ProductionServices


class FrozenRetryTest(unittest.TestCase):
    def task(self):
        return {'id':'failed_landsat','status':'failed','plan':{'requirement':'分析所选区域2025年11月的NDMI指数','mode':'sentinel_cog','region':'选区','aoi':{'bbox':[114.2,30.5,114.21,30.51]},'goal':'ndvi','threshold':.2,'cloud':20,'direction':'absolute','periods':{'before':['2025-10-31','2025-10-31'],'after':['2025-11-01','2025-11-30']},'sampling':'native','index_id':'ndmi','source_id':'pc_landsat_c2_l2','scene_id':'LC09_L2SP_123039_20251129_02_T1','observation_ref':{'id':'locked_observation','metadata_sha256':'a'*64,'collection':'landsat-c2-l2','item_id':'LC09_L2SP_123039_20251129_02_T1'}}}
    def test_landsat_retry_preserves_selected_source_scene_and_frozen_version_without_aliasing(self):
        task=self.task();before=copy.deepcopy(task);store=types.SimpleNamespace(get=Mock(return_value=task),create=Mock(side_effect=lambda body:body))
        body=ProductionServices.retry(store,task['id'])
        for key in ('source_id','scene_id','observation_ref'):self.assertEqual(body[key],task['plan'][key])
        self.assertNotIn('Landsat',body['requirement']);body['observation_ref']['metadata_sha256']='changed returned request';body['periods']['after'][0]='2026-01-01'
        self.assertEqual(task,before)
    def test_legacy_retry_does_not_invent_an_observation_or_provider(self):
        task=self.task()
        for key in ('source_id','scene_id','observation_ref'):del task['plan'][key]
        store=types.SimpleNamespace(get=Mock(return_value=task),create=Mock(side_effect=lambda body:body))
        body=ProductionServices.retry(store,task['id']);self.assertFalse(any(k in body for k in ('source_id','scene_id','observation_ref')))
    def test_successful_task_cannot_be_retried_and_create_is_not_called(self):
        task=self.task();task['status']='succeeded';store=types.SimpleNamespace(get=Mock(return_value=task),create=Mock())
        with self.assertRaises(GeoError):ProductionServices.retry(store,task['id'])
        store.create.assert_not_called()


if __name__=='__main__':unittest.main()
