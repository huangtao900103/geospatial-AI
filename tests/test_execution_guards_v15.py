"""Cancellation at dispatch and cross-product source guards, isolated fixtures."""
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.server import Workspace
import test_execution as fixtures
from backend.analysis.cropland_services import make_plan as crop_plan,product_profile
from backend.workflows.execution_answers import build_answer


class DispatchCancellationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.w=Workspace(self.root/'workspace.sqlite3')
        self.w.landcover.close();self.lc=fixtures.ControlledLC(self.w,self.root/'lc');self.w.landcover=self.lc;self.lc.gate=threading.Event()
        self.boundary=patch('backend.workflows.execution_services.load_boundary',return_value=({}, {'sha256':'b'*64,'bbox':[112.9,30.1,113.8,30.5]}));self.boundary.start()
    def tearDown(self):self.lc.gate.set();self.w.close();self.boundary.stop();self.temp.cleanup()
    def test_cancel_after_child_creation_before_former_process_binding_stops_owned_child(self):
        original=self.w.executions.bind_async;dispatched=threading.Event();release=threading.Event()
        def delayed_return(*args):
            value=original(*args);dispatched.set();release.wait(3);return value
        with patch.object(self.w.executions,'bind_async',side_effect=delayed_return):
            job=self.w.executions.create({'question':'仙桃农田有多少亩'})
            self.assertTrue(dispatched.wait(3),'dispatch did not happen')
            bound=self.w.executions.get(job['id'])['binding'];self.assertIsNotNone(bound)
            self.assertEqual(self.w.executions.get(job['id'])['tool_calls'][0]['binding']['task_id'],bound['task_id'])
            self.w.executions.cancel(job['id']);self.assertEqual(self.lc.get(bound['task_id'])['status'],'cancelled')
            release.set()
        self.assertEqual(self.w.executions.get(job['id'])['status'],'cancelled')


class ProductContractTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.w=Workspace(Path(self.temp.name)/'workspace.sqlite3');self.bbox=[113.3,30.3,113.31,30.31]
    def tearDown(self):self.w.close();self.temp.cleanup()
    def test_explicit_2024_and_structured_product_select_real_registered_product(self):
        for context in ({'bbox':self.bbox},{'bbox':self.bbox,'cropland_product':'io_lulc_2024'}):
            plan=self.w.executions.prepare('标注选区2024年农田',context);self.assertTrue(plan['executable'],plan);self.assertEqual(plan['tool']['inputs']['product'],'io_lulc_2024')
    def test_year_product_conflicts_and_unregistered_product_never_fall_back(self):
        for question,product in [('标注选区2021年农田','io_lulc_2024'),('标注选区2024年农田','worldcover_2021_v200'),('标注选区农田','unknown'),('标注选区农田',{})]:
            plan=self.w.executions.prepare(question,{'bbox':self.bbox,'cropland_product':product});self.assertFalse(plan['executable']);self.assertEqual(plan['status'],'unsupported')
    def test_cache_identity_separates_product_year_class_and_area_contract(self):
        wc={'bbox':self.bbox,'product':'worldcover_2021_v200'};io={'bbox':self.bbox,'product':'io_lulc_2024'}
        self.assertNotEqual(self.w.executions.input_key('cropland_delineation',wc),self.w.executions.input_key('cropland_delineation',io))
        profile=product_profile('io_lulc_2024');plan=crop_plan({**io,'requirement':'标注选区2024年农田'})
        task={'id':'fixture_io','status':'succeeded','plan':plan,'result':{'summary':{'reference_year':2024,'patch_count':0},'provenance':{'product':profile,'statistics_resampled':False,'area_method':profile['area_method']},'geojson':{'type':'FeatureCollection','features':[]}}}
        with patch.object(self.w.cropland,'artifact',return_value=Path('test-only-file')):
            key=self.w.executions.input_key('cropland_delineation',io)
            self.assertTrue(self.w.executions.compatible_child('cropland_delineation',task,io,key))
            self.assertFalse(self.w.executions.compatible_child('cropland_delineation',task,wc,self.w.executions.input_key('cropland_delineation',wc)))
            changed=copy.deepcopy(task);changed['result']['summary']['reference_year']=2021
            self.assertFalse(self.w.executions.compatible_child('cropland_delineation',changed,io,key))
            changed=copy.deepcopy(task);changed['result']['provenance']['area_method']='wgs84_ellipsoid_latitude_band'
            self.assertFalse(self.w.executions.compatible_child('cropland_delineation',changed,io,key))
            for target,field,value in [('summary','class_code',40),('provenance','class_code',40),('summary','source_id','worldcover_2021'),('provenance','source_id','worldcover_2021'),('summary','area_method','wgs84_ellipsoid_latitude_band')]:
                changed=copy.deepcopy(task);changed['result'][target][field]=value;self.assertFalse(self.w.executions.compatible_child('cropland_delineation',changed,io,key))
            old_wc={'id':'fixture_wc','status':'succeeded','plan':crop_plan({**wc,'requirement':'标注选区2021年农田'}),'result':{'summary':{'reference_year':2021,'patch_count':0},'provenance':{'product':product_profile('worldcover_2021_v200'),'statistics_resampled':False},'geojson':{'type':'FeatureCollection','features':[]}}}
            wc_key=self.w.executions.input_key('cropland_delineation',wc);self.assertTrue(self.w.executions.compatible_child('cropland_delineation',old_wc,wc,wc_key))
            old_wc['result']['provenance']['area_method']='native_utm_projected_grid';self.assertFalse(self.w.executions.compatible_child('cropland_delineation',old_wc,wc,wc_key))
    def test_answer_uses_actual_product_year_class_and_portable_bundle(self):
        profile=product_profile('io_lulc_2024');result={'summary':{'reference_year':2024,'cropland_ha':1.2,'cropland_mu':18,'patch_count':3,'coverage_ratio':.8,'nodata_pixel_count':4},'provenance':{'product':profile,'year':2024,'area_method':profile['area_method']},'report':{'limitations':profile['limitations']},'artifacts':[]}
        answer=build_answer({'id':'fixture_question'},{'id':'fixture_execution','binding':{'type':'cropland','task_id':'fixture_io'},'result_ref':{'type':'cropland','task_id':'fixture_io','url':'/api/cropland/tasks/fixture_io'},'result':result,'summary':'test fixture only'})
        self.assertEqual(answer['source_year'],2024);self.assertIn('2024年产品',answer['summary']);self.assertIn('类别5',answer['summary']);self.assertNotIn('ESA WorldCover',answer['source_description']);self.assertIn('未知像元',''.join(answer['warnings']));self.assertEqual(answer['downloads'][-1]['type'],'zip');self.assertEqual(answer['downloads'][-1]['url'],'/api/results/cropland/fixture_io/bundle.zip')
    def test_generic_cropland_questions_with_explicit_map_area_never_use_city_total(self):
        for question in ['农田有多少亩','农田分布','农田情况','统计所选区域的农田面积','查询选中区域2024年农田面积']:
            with self.subTest(question=question):
                plan=self.w.executions.prepare(question,{'bbox':self.bbox});self.assertTrue(plan['executable'],plan);self.assertEqual(plan['tool']['id'],'cropland_delineation');self.assertEqual(plan['tool']['inputs']['bbox'],self.bbox)
        plan=self.w.executions.prepare('农田有多少亩',{'bbox':self.bbox,'cropland_product':'io_lulc_2024'});self.assertEqual(plan['tool']['inputs']['product'],'io_lulc_2024')
    def test_selected_area_business_guards_keep_rejecting_official_current_and_crop_subclasses(self):
        for question in ['统计所选区域官方农田面积','所选区域今年农田有多少亩','查询选中区域水稻农田面积','查询所选区域农田变化面积']:
            with self.subTest(question=question):
                plan=self.w.executions.prepare(question,{'bbox':self.bbox});self.assertFalse(plan['executable']);self.assertEqual(plan['status'],'needs_data' if '水稻' in question else 'unsupported')
    def test_city_name_without_selected_area_confirmation_requires_scope_and_full_city_stays_explicit(self):
        for question in ['仙桃市农田有多少亩','仙桃农田有多少亩','武汉农田有多少亩']:
            plan=self.w.executions.prepare(question,{'bbox':self.bbox});self.assertFalse(plan['executable']);self.assertEqual(plan['status'],'needs_data');self.assertEqual(plan['missing_fields'],['scope_confirmation'])
        full=self.w.executions.prepare('仙桃全市农田有多少亩',{'bbox':self.bbox});self.assertTrue(full['executable'],full);self.assertEqual(full['tool']['id'],'landcover_area')
        conflict=self.w.executions.prepare('仙桃全市农田有多少亩',{'bbox':self.bbox,'cropland_product':'io_lulc_2024'});self.assertFalse(conflict['executable']);self.assertEqual(conflict['status'],'unsupported')
        legacy=self.w.executions.prepare('仙桃农田有多少亩',{});self.assertTrue(legacy['executable']);self.assertEqual(legacy['tool']['id'],'landcover_area')
    def test_source_reference_and_spectral_questions_are_not_converted_to_cropland_extraction(self):
        self.w.save('execution',{'id':'fixture_reference','status':'succeeded','created_at':'2026-11-14','result':{'summary':{}},'result_ref':{'type':'cropland','task_id':'fixture_io','url':'/api/cropland/tasks/fixture_io'},'binding':{'type':'cropland','task_id':'fixture_io'}})
        reference=self.w.executions.prepare('这些农田数据来源是什么',{'bbox':self.bbox,'reference_execution_id':'fixture_reference'});self.assertTrue(reference['executable']);self.assertEqual(reference['tool']['id'],'result_reference')
        spectral=self.w.executions.prepare('查看所选区域农田的NDVI',{'bbox':self.bbox,'period':{'start':'2025-11-01','end':'2025-11-30'}});self.assertTrue(spectral['executable'],spectral);self.assertEqual(spectral['tool']['id'],'sentinel_native')
    def test_scope_confirmation_full_city_continuation_cannot_drop_explicit_2024_product(self):
        for question in ['仙桃全市耕地有多少亩','仙桃市农田有多少亩；补充：全市']:
            with self.subTest(question=question):
                plan=self.w.executions.prepare(question,{'bbox':self.bbox,'cropland_product':'io_lulc_2024'});self.assertFalse(plan['executable']);self.assertEqual(plan['status'],'unsupported');self.assertIn('不能被静默改为2021',plan['blocking_gaps'][0])
        pending=self.w.executions.prepare('仙桃市农田有多少亩',{'bbox':self.bbox,'cropland_product':'io_lulc_2024'});self.assertEqual(pending['missing_fields'],['scope_confirmation']);self.assertIn('按地图选区分析',pending['clarification_message']);self.assertIn('全市/全县',pending['clarification_message'])
        selected=self.w.executions.prepare('仙桃市农田有多少亩；补充：按地图选区分析',{'bbox':self.bbox,'cropland_product':'io_lulc_2024'});self.assertTrue(selected['executable']);self.assertEqual(selected['tool']['inputs']['product'],'io_lulc_2024')


if __name__=='__main__':unittest.main()
