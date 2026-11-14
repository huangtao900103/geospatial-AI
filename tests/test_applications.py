"""Business template validation and real planner handoff, without remote I/O."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from server import Workspace
from geo_services import GeoError

class ApplicationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.w=Workspace(Path(self.tmp.name)/'workspace.sqlite3');self.launch=patch.object(self.w.assistant,'launch');self.launcher=self.launch.start()
    def tearDown(self):self.launch.stop();self.w.close();self.tmp.cleanup()
    def body(self,id='water_clues',**overrides):
        app=self.w.applications.get(id);context=copy.deepcopy(app['defaults']['context'])
        if app['form']['requires_bbox']:context['bbox']=[114.2,30.5,114.21,30.51]
        return {'application_id':id,'context':context,**overrides}
    def test_catalog_exposes_only_executable_business_and_separate_professional_tools(self):
        result=self.w.applications.catalog();self.assertEqual(len(result['applications']),17);self.assertEqual(sum(a['featured'] for a in result['applications']),16)
        self.assertEqual(self.w.applications.get('land_resources')['scope'],'fixed_region');self.assertIn('2021',self.w.applications.get('land_resources')['scope_label']);self.assertFalse(self.w.applications.get('spectral_lab')['featured'])
        self.assertFalse(self.w.list('assistant_question'));self.assertFalse(self.w.list('production_task'))
    def test_all_templates_prepare_to_correct_real_tools_not_fixture_runs(self):
        expected={'land_resources':'landcover_area','vegetation_status':'sentinel_native','vegetation_change':'sentinel_native','water_clues':'sentinel_native','vegetation_moisture':'sentinel_native','imagery_search':'public_catalog_search','weather':'weather_forecast','layer_summary':'vector_analysis','spectral_lab':'sentinel_native','crop_spectral':'sentinel_native','crop_rededge':'sentinel_native','soil_spectral':'sentinel_native'}
        data=self.w.create_dataset({'name':'用户实测点输入测试','geojson':{'type':'FeatureCollection','features':[{'type':'Feature','properties':{'name':'point'},'geometry':{'type':'Point','coordinates':[114.2,30.5]}}]}})
        for id,tool in expected.items():
            with self.subTest(id=id):
                body=self.body(id)
                if id=='layer_summary':body['context']['dataset_id']=data['id']
                payload,ref=self.w.applications.prepare(body);plan=self.w.executions.prepare(payload['question'],payload['context']);self.assertTrue(plan['executable'],plan);self.assertEqual(plan['tool']['id'],tool)
                if tool=='sentinel_native':self.assertEqual(plan['tool']['inputs']['sampling'],'native')
        self.assertFalse(self.w.list('execution'));self.assertFalse(self.w.list('production_task'))
    def test_comparison_and_decimal_precision_survive_handoff(self):
        for operator in ('gt','gte','lt','lte'):
            with self.subTest(operator=operator):
                payload,ref=self.w.applications.prepare(self.body(parameters={'threshold':.123456789123456,'operator':operator}));plan=self.w.executions.prepare(payload['question'],payload['context']);selection=plan['source_snapshot']['selection'];self.assertEqual(selection['operator'],operator);self.assertEqual(selection['threshold'],.123456789123456)
        payload,_=self.w.applications.prepare(self.body(parameters={'threshold':1e-10,'operator':'gte'}));selection=self.w.executions.prepare(payload['question'],payload['context'])['source_snapshot']['selection'];self.assertEqual(selection['threshold'],1e-10)
    def test_fixed_region_never_silently_uses_or_discards_other_scope(self):
        for context in ({'bbox':[114.2,30.5,114.21,30.51]},{'period':{'start':'2026-01-01','end':'2026-12-31'}},{'dataset_id':'someone'}):
            with self.subTest(context=context),self.assertRaises(GeoError):self.w.applications.prepare({'application_id':'land_resources','context':context})
        payload,_=self.w.applications.prepare(self.body('land_resources'));self.assertEqual(payload['context'],{});self.assertIn('2021',payload['question']);self.assertIn('仙桃',payload['question'])
    def test_area_dates_thresholds_and_unknown_fields_fail_before_any_job(self):
        cases=[self.body(context={}),self.body(context={'bbox':[114,30,115,31],'period':{'start':'2025-11-01','end':'2025-11-30'}}),self.body(context={'bbox':[True,30,114.21,30.51],'period':{'start':'2025-11-01','end':'2025-11-30'}}),self.body(context={'bbox':[114.2,30.5,114.21,30.51],'period':{'start':'2025-11-30','end':'2025-11-01'}}),self.body(parameters={'threshold':float('nan')}),self.body(parameters={'threshold':True}),self.body(parameters={'operator':'eval'}),self.body(parameters={'threshold':2}),self.body(parameters={'url':'https://x/'}),{**self.body(),'question':'覆盖模板'}]
        for body in cases:
            with self.subTest(body=str(body)),self.assertRaises(GeoError):self.w.applications.run(body)
        self.assertFalse(self.w.list('assistant_question'));self.assertEqual(self.launcher.call_count,0)
    def test_two_periods_are_complete_ordered_and_frozen(self):
        payload,ref=self.w.applications.prepare(self.body('vegetation_change'));plan=self.w.executions.prepare(payload['question'],payload['context']);self.assertEqual(plan['source_snapshot']['periods'],ref['context']['periods']);self.assertEqual(plan['source_snapshot']['direction'],'decrease');self.assertEqual(plan['source_snapshot']['selection']['operator'],'gt');self.assertEqual(plan['source_snapshot']['threshold'],.1)
        for value in ({'before':['2025-11-01','2025-11-30'],'after':['2025-10-01','2025-10-31']},{'before':['2025-10-01'],'after':['2025-11-01','2025-11-30']}):
            with self.assertRaises(GeoError):self.w.applications.prepare(self.body('vegetation_change',context={'bbox':[114.2,30.5,114.21,30.51],'periods':value}))
    def test_application_snapshot_exists_before_thread_launch_and_is_owned_copy(self):
        body=self.body(request_id='app-one')
        def launch(id):self.assertEqual(self.w.assistant.raw_get(id)['application_ref']['id'],'water_clues')
        self.launcher.side_effect=launch;q=self.w.applications.run(body);saved=copy.deepcopy(q['application_ref']);body['context']['bbox'][0]=0
        self.assertEqual(self.w.assistant.get(q['id'])['application_ref'],saved);self.assertIn('MNDWI',q['question']);self.assertEqual(q['status'],'processing');self.assertIsNone(q['answer'])
    def test_request_replay_conflicts_include_template_not_only_canonical_question(self):
        a=self.w.applications.run(self.body('vegetation_status',request_id='same'));b=self.w.applications.run(self.body('vegetation_status',request_id='same'));self.assertEqual(a['id'],b['id']);self.assertEqual(self.launcher.call_count,1)
        with self.assertRaises(GeoError) as e:self.w.applications.run(self.body('spectral_lab',request_id='same',parameters={'index_id':'ndvi'}))
        self.assertEqual(e.exception.status,409)
    def test_user_layer_required_fixture_rejected_and_source_version_frozen(self):
        with self.assertRaises(GeoError):self.w.applications.prepare({'application_id':'layer_summary','context':{'dataset_id':'xt-fixture'}})
        data=self.w.create_dataset({'name':'user','geojson':{'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Point','coordinates':[114,30]},'properties':{}}]}});_,ref=self.w.applications.prepare({'application_id':'layer_summary','context':{'dataset_id':data['id']}});self.assertEqual(ref['method']['dataset']['sha256'],data['sha256'])
    def test_retry_retains_original_application_and_business_parameters(self):
        q=self.w.applications.run(self.body());q.update(status='failed');self.w.save('assistant_question',q);again=self.w.assistant.retry(q['id']);self.assertNotEqual(again['id'],q['id']);self.assertEqual(again['application_ref'],q['application_ref']);self.assertEqual(again['context'],q['context'])
    def test_cancel_during_execution_creation_stops_unlinked_job(self):
        q=self.w.applications.run(self.body())
        def creating(*args,**kwargs):
            self.w.assistant.cancel(q['id'])
            return {'id':'execution_race','status':'running'}
        with patch.object(self.w.executions,'create',side_effect=creating),patch.object(self.w.executions,'cancel') as cancel:
            self.w.assistant.process(q['id'])
        cancel.assert_called_once_with('execution_race');current=self.w.assistant.get(q['id']);self.assertEqual(current['status'],'cancelled');self.assertIsNone(current['answer']);self.assertEqual(current['application_ref'],q['application_ref'])
    def test_layer_answer_uses_readable_statistics_and_direct_geojson_download(self):
        from execution_answers import build_answer
        result={'feature_count':2,'summary':{'geometry_counts':{'LineString':2},'polygon_feature_count':0,'area_sum_ha':0},'provenance':{'dataset_name':'参考水系'}}
        answer=build_answer({'id':'q'},{'id':'e','intent':'user_vector_analysis','binding':{'type':'analysis','task_id':'a'},'result_ref':{'url':'/api/analysis/a'},'result':result,'summary':'空间统计已完成'})
        self.assertIn('2个线要素',answer['summary']);self.assertNotIn('geometry_counts',answer['summary']);self.assertEqual(answer['chart']['data'][0]['label'],'线要素');self.assertEqual(answer['downloads'][0]['url'],'/api/analysis/a/result.geojson');self.assertFalse(any(m['id']=='area_sum_ha' for m in answer['metrics']))

if __name__=='__main__':unittest.main()
