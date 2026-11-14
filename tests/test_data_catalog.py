import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from geo_services import GeoError
from landcover_services import PRODUCT,load_boundary
from production_services import make_plan
from server import Workspace


class CatalogTest(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.w=Workspace(Path(self.tmp.name)/'workspace.sqlite3');self.catalog=self.w.data_catalog
    def tearDown(self):self.w.close();self.tmp.cleanup()
    def seed_landcover(self):
        _,boundary=load_boundary();task={'id':'lc_evidence','status':'succeeded','finished_at':'2026-11-13','created_at':'2026-11-13','result':{'provenance':{'product':PRODUCT,'year':2021,'boundary_source':boundary,'boundary_sha256':boundary['sha256'],'classified_window_sha256':'a'*64}}};self.w.save('landcover_task',task);return {'task_type':'landcover','task_id':task['id']}
    def test_stages_modality_authorization_and_nonpixel_are_separate(self):
        sources={s['id']:s for s in self.catalog.sources()['sources']}
        sar=sources['pc_sentinel1_rtc'];self.assertTrue(sar['stages']['catalog_searchable']);self.assertFalse(sar['stages']['pixel_readable']);self.assertFalse(sar['stages']['analysis_ready']);self.assertTrue(sar['auth']['account_required'])
        self.assertEqual(sar['resolution']['native']['pixel_spacing_m'],10);self.assertEqual(sar['resolution']['native']['range_resolution_m'],20)
        self.assertFalse(sources['open_meteo']['stages']['pixel_applicable']);self.assertEqual(sources['open_meteo']['coverage']['temporal']['time_role'],'forecast')
        for id in ('ogc_external','business_systems','iot_observations','domestic_highres_planned','uav_ortho_planned','dem_terrain_planned'):
            for stage in ('catalog_searchable','pixel_readable','analysis_ready'):self.assertFalse(sources[id]['stages'][stage])
        self.assertEqual(sources['pc_landsat_c2_l2']['license']['reported_value'],'proprietary');self.assertIn('usgs.gov',sources['pc_landsat_c2_l2']['license']['url'])
    def test_metadata_fingerprint_is_stable_across_verification(self):
        first=self.catalog.get_source('pc_sentinel2_l2a')['metadata_fingerprint'];self.w.save('data_source_check',{'id':'pc_sentinel2_l2a','verification':{'operation':'catalog_search','status':'failed','checked_at':'now'}})
        self.assertEqual(first,self.catalog.get_source('pc_sentinel2_l2a')['metadata_fingerprint'])
        p=self.catalog.create_plan({'goal':'cropland_area','period':{'start':'2021-01-01','end':'2021-12-31'}});self.assertTrue(p['source_snapshot']);self.assertTrue(p['catalog_fingerprint']);self.assertTrue(p['not_executed'])
    def test_new_connection_checks_preserve_previous_snapshot(self):
        old={'id':'pc_sentinel2_l2a','verification':{'operation':'catalog_search','status':'failed','checked_at':'2026-01-01T00:00:00Z'},'created_at':'2026-01-01T00:00:00Z'}
        self.w.save('data_source_check',old);self.catalog.reader.fetch_json=Mock(return_value={'features':[]})
        self.catalog.search({'source_ids':['pc_sentinel2_l2a'],'bbox':[114.2,30.5,114.21,30.51],'start':'2025-11-01','end':'2025-11-30'})
        self.assertEqual(self.w.get('data_source_check','pc_sentinel2_l2a'),old)
        self.assertEqual(self.catalog.get_source('pc_sentinel2_l2a')['verification']['status'],'empty')
        self.assertEqual(len(self.w.list('data_source_check')),2)
    def test_user_geojson_catalog_carries_actual_extent_not_observation_time(self):
        data=self.w.create_dataset({'name':'User only','geojson':{'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':{'type':'Point','coordinates':[113.3,30.3]}}]}})
        s=self.catalog.get_source('user_geojson:'+data['id']);self.assertEqual(s['coverage']['spatial']['bbox'],[113.3,30.3,113.3,30.3]);self.assertIsNone(s['coverage']['temporal']['start']);self.assertEqual(s['provenance']['source_version'],data['sha256'])
    def test_draft_does_not_connect_or_store_credentials(self):
        self.catalog.reader.fetch_json=Mock(side_effect=AssertionError('network must not be called'))
        draft=self.catalog.create_draft({'name':'OGC draft','source_type':'ogc','endpoint':'https://example.test/wms?layers=farms','protocol':'WMS'})
        self.assertFalse(draft['stages']['analysis_ready']);self.assertEqual(draft['stages']['connection_status'],'registered_not_connected');self.catalog.reader.fetch_json.assert_not_called()
        for endpoint in ['https://u:p@example.test/a','https://example.test/a?token=','https://example.test/a?sv=2025&sig=unit-test-secret','https://example.test/a?X-Amz-Signature=unit-test-secret','https://example.test/a?x-goog-signature=unit-test-secret']:
            with self.assertRaises(GeoError):self.catalog.create_draft({'name':'bad','source_type':'catalog','endpoint':endpoint,'protocol':'STAC'})
    def test_search_is_fixed_bounded_metadata_only_and_snapshotted(self):
        data={'features':[{'id':'scene1','collection':'sentinel-2-l2a','bbox':[113.2,30.2,113.4,30.4],'properties':{'datetime':'2025-10-20T00:00:00Z','eo:cloud_cover':3,'platform':'S2'},'assets':{'B04':{'href':'https://example.test/a?sig=unit-test-secret'}}}]}
        self.catalog.reader.fetch_json=Mock(return_value=data)
        result=self.catalog.search({'source_ids':['pc_sentinel2_l2a'],'bbox':[113.3,30.3,113.31,30.31],'start':'2025-10-01','end':'2025-10-31','limit':1})
        self.assertEqual(result['status'],'completed');self.assertTrue(result['discovery_only']);self.assertEqual(result['results'][0]['items'][0]['asset_roles'],['B04']);self.assertNotIn('unit-test-secret',json.dumps(result));self.assertEqual(len(result['results'][0]['provenance']['response_sha256']),64)
        self.assertEqual(self.catalog.get_search(result['id']),result);self.assertTrue(self.catalog.reader.fetch_json.call_args[0][0].startswith('https://planetarycomputer.microsoft.com/api/stac/v1/search?'))
        self.assertEqual(self.catalog.get_source('pc_sentinel2_l2a')['verification']['operation'],'catalog_search')
        for body in [{'source_ids':['https://evil.test']},{'source_ids':['pc_sentinel2_l2a'],'limit':True},{'source_ids':['pc_sentinel2_l2a'],'bbox':[0,0,100,100]}]:
            with self.assertRaises(GeoError):self.catalog.search({'bbox':[113.3,30.3,113.31,30.31],'start':'2025-10-01','end':'2025-10-31',**body})
    def test_partial_failures_empty_and_wrong_scope_never_fake_success(self):
        self.catalog.reader.fetch_json=Mock(side_effect=[{'features':[]},GeoError('UPSTREAM_FAILED','no source',502)])
        r=self.catalog.search({'source_ids':['pc_sentinel2_l2a','pc_sentinel1_rtc'],'bbox':[113.3,30.3,113.31,30.31],'start':'2025-10-01','end':'2025-10-31'})
        self.assertEqual(r['status'],'partial');self.assertEqual(r['results'][0]['status'],'empty');self.assertEqual(r['results'][1]['status'],'failed');self.assertEqual(r['results'][1]['items'],[])
    def test_historical_source_not_substituted_for_current_area(self):
        for q in ['统计2026年仙桃耕地面积','今日仙桃全市耕地面积','本年仙桃耕地面积','目前仙桃农田面积']:
            p=self.catalog.create_plan({'question':q});self.assertFalse(p['executable']);self.assertIsNone(p['execution_contract']);self.assertIn('YEAR_MISMATCH',[g['code'] for g in p['gaps']]);self.assertEqual(next(c for c in p['candidates'] if c['source_id']=='worldcover_2021')['suitability'],'rejected')
        for q in ['统计湖北省2021年耕地面积','2021年全湖北农田面积','用遥感统计2021年武汉市全市农田面积']:
            p=self.catalog.create_plan({'question':q});self.assertFalse(p['executable']);self.assertIn('REGION_UNSUPPORTED',[g['code'] for g in p['gaps']])
    def test_business_area_definitions_do_not_become_worldcover_execution(self):
        for metric in ('高标准农田','官方耕地','法定耕地','播种','水田','旱地','水稻','小麦'):
            p=self.catalog.create_plan({'question':'统计2021年仙桃'+metric+'面积','goal':'cropland_area'})
            self.assertFalse(p['executable'],metric);self.assertIsNone(p['execution_contract']);self.assertIn('METRIC_UNSUPPORTED',[g['code'] for g in p['gaps']])
        p=self.catalog.create_plan({'question':'统计2021年仙桃播种面积','bbox':[113.3,30.3,113.31,30.31],'period':{'start':'2021-01-01','end':'2021-12-31'}})
        self.assertFalse(p['executable']);self.assertIn('METRIC_UNSUPPORTED',[g['code'] for g in p['gaps']])
    def test_explicit_month_or_iso_dates_cannot_be_overridden_by_period(self):
        box=[113.3,30.3,113.31,30.31]
        for question in ('分析2025年11月样区NDVI','分析2025-11-01至2025-11-30样区NDVI','分析2025-11-10样区NDVI'):
            p=self.catalog.create_plan({'question':question,'bbox':box,'period':{'start':'2025-10-01','end':'2025-10-31'}})
            self.assertFalse(p['executable'],question);self.assertIsNone(p['execution_contract']);self.assertIn('PERIOD_CONFLICT',[g['code'] for g in p['gaps']])
        p=self.catalog.create_plan({'question':'分析2025年11月样区NDVI','bbox':box,'period':{'start':'2025-11-01','end':'2025-11-30'}})
        self.assertTrue(p['executable']);self.assertEqual(make_plan(p['execution_contract']['body'])['periods']['after'],['2025-11-01','2025-11-30'])
        p=self.catalog.create_plan({'question':'统计2021年10月仙桃耕地面积'})
        self.assertFalse(p['executable']);self.assertIn('ANNUAL_PRODUCT_ONLY',[g['code'] for g in p['gaps']])
    def test_goal_and_source_constraints_do_not_bypass_modalities(self):
        p=self.catalog.create_plan({'question':'统计2026年仙桃全市农田面积','goal':'ndvi','bbox':[113.3,30.3,113.31,30.31],'period':{'start':'2026-10-01','end':'2026-10-31'}});self.assertFalse(p['executable']);self.assertIn('GOAL_CONFLICT',[g['code'] for g in p['gaps']])
        p=self.catalog.create_plan({'question':'计算NDVI','goal':'ndvi','source_ids':['pc_sentinel1_rtc'],'bbox':[113.3,30.3,113.31,30.31],'period':{'start':'2025-10-01','end':'2025-10-31'}});self.assertFalse(p['executable']);self.assertIn('OPTICAL_BANDS_REQUIRED',[g['code'] for g in p['gaps']]);self.assertEqual(p['candidates'][0]['suitability'],'rejected')
    def test_registered_only_sources_never_make_executable_empty_discovery(self):
        for source_id in ('ogc_external','business_systems','iot_observations','domestic_highres_planned','uav_ortho_planned','dem_terrain_planned'):
            p=self.catalog.create_plan({'goal':'general_discovery','source_ids':[source_id],'bbox':[113.3,30.3,113.31,30.31],'period':{'start':'2025-10-01','end':'2025-10-31'}})
            self.assertEqual(p['selection'],[]);self.assertFalse(p['executable']);self.assertIsNone(p['execution_contract']);self.assertIn('NO_SEARCHABLE_SOURCE',[g['code'] for g in p['gaps']],source_id)
    def test_single_ndvi_contract_preserves_requested_observation_period(self):
        p=self.catalog.create_plan({'question':'计算NDVI','goal':'ndvi','bbox':[113.3,30.3,113.31,30.31],'period':{'start':'2025-10-01','end':'2025-10-31'}})
        self.assertTrue(p['executable']);actual=make_plan(p['execution_contract']['body']);self.assertEqual(actual['periods']['after'],['2025-10-01','2025-10-31'])
        self.assertTrue(p['not_executed']);self.assertEqual(p['used'],[])
    def test_weather_is_forecast_not_arbitrary_history_or_wrong_city(self):
        p=self.catalog.create_plan({'question':'2021年仙桃天气','goal':'weather'});self.assertFalse(p['executable']);self.assertIn('HISTORICAL_WEATHER_UNSUPPORTED',[g['code'] for g in p['gaps']])
        p=self.catalog.create_plan({'question':'武汉天气'});self.assertFalse(p['executable']);self.assertEqual(p['interpreted']['region'],'武汉市')
    def test_success_context_is_actual_evidence_not_new_catalog_candidates(self):
        context=self.seed_landcover();self.catalog.reader.fetch_json=Mock(side_effect=AssertionError('planning is offline'))
        p=self.catalog.create_plan({'question':'仙桃农田有多少亩','context':context});self.assertEqual({u['source_id'] for u in p['used']},{'worldcover_2021','xiantao_boundary_2017'});self.assertEqual(p['selection'],[]);self.assertTrue(p['not_executed']);self.assertFalse(p['executable']);self.assertEqual(p['provenance']['purpose'],'lineage_only');self.catalog.reader.fetch_json.assert_not_called()
        for question in ('市界是2017年的吗，来源是什么','仙桃水域占比多少','这些数据准吗','树木覆盖面积多少','下载已有结果'):
            p=self.catalog.create_plan({'question':question,'context':context});self.assertEqual(len(p['used']),2,question);self.assertIsNone(p['related_existing_result'],question)
    def test_mismatched_context_never_counts_as_used(self):
        context=self.seed_landcover()
        for q in ['2026年仙桃耕地面积多少','当前仙桃耕地面积多少','武汉市2021农田面积']:
            p=self.catalog.create_plan({'question':q,'context':context});self.assertEqual(p['used'],[]);self.assertIsNotNone(p['related_existing_result'])
        p=self.catalog.create_plan({'question':'当前可用来源是哪年','context':context});self.assertEqual(len(p['used']),2)
        p=self.catalog.create_plan({'question':'2021年10月仙桃农田面积','context':context});self.assertEqual(p['used'],[]);self.assertIsNotNone(p['related_existing_result']);self.assertIn('ANNUAL_PRODUCT_ONLY',[g['code'] for g in p['gaps']])
        p=self.catalog.create_plan({'question':'计算NDVI','goal':'ndvi','bbox':[113.3,30.3,113.31,30.31],'period':{'start':'2021-01-01','end':'2021-10-31'},'context':context});self.assertEqual(p['used'],[])
    def test_monthly_context_does_not_turn_annual_product_into_monthly_evidence(self):
        context=self.seed_landcover()
        for question in ('2021年10月仙桃农田面积多少','统计2021-10-01至2021-10-31仙桃耕地面积'):
            p=self.catalog.create_plan({'question':question,'context':context});self.assertEqual(p['used'],[]);self.assertIsNotNone(p['related_existing_result']);self.assertFalse(p['executable'])
        p=self.catalog.create_plan({'question':'市界是2017年的吗，数据来自哪里','context':context});self.assertEqual(len(p['used']),2)
    def test_plan_snapshot_is_immutable_after_catalog_check_update(self):
        p=self.catalog.create_plan({'goal':'cropland_area','period':{'start':'2021-01-01','end':'2021-12-31'}});before=copy.deepcopy(p['source_snapshot'])
        self.w.save('data_source_check',{'id':'worldcover_2021','verification':{'status':'failed','checked_at':'later'}});self.assertEqual(self.catalog.get_plan(p['id'])['source_snapshot'],before)
    def test_different_structured_period_cannot_override_question_year(self):
        p=self.catalog.create_plan({'question':'统计2026年仙桃耕地面积','period':{'start':'2021-01-01','end':'2021-12-31'}});self.assertFalse(p['executable']);self.assertIn('PERIOD_CONFLICT',[g['code'] for g in p['gaps']])


if __name__=='__main__':unittest.main()
