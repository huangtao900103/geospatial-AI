import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
from geo_services import GeoError
from production_services import make_plan as original_make_plan
from execution_services import ExecutionServices
from native_raster import VERSION as NATIVE_VERSION
from server import Workspace
from test_assistant import FakeLandcover


def wait_job(service,id):
    for _ in range(400):
        job=service.get(id)
        if job['status'] in ('succeeded','failed','cancelled','unsupported','needs_data'):return job
        time.sleep(.01)
    raise AssertionError('job did not finish')


def native_plan(body):
    raw={k:v for k,v in body.items() if k!='sampling'};p=original_make_plan(raw);p['sampling']=body.get('sampling','representative');return p


class ControlledLC(FakeLandcover):
    def create(self,body):
        self.created+=1;id='lc_job_'+str(self.created);area=self.next_area
        t={'id':id,'status':'running','progress':10,'plan':{'reference_year':2021},'result':None,'created_at':str(time.time_ns()),'nodes':[{'id':'account','name':'真实统计节点','status':'running'}]};self.store.save('landcover_task',t)
        def run():
            if self.gate:self.gate.wait(2)
            if self.get(id)['status']=='cancelled':return
            self.seed(id,area)
        thread=threading.Thread(target=run,daemon=True);self.threads.append(thread);thread.start();return t
    def cancel(self,id):
        t=self.get(id)
        if t['status'] not in ('queued','running'):raise GeoError('INVALID_STATE','already terminal',409)
        t['status']='cancelled';self.store.save('landcover_task',t);return t


class FakeProduction:
    def __init__(self,store,root):self.store=store;self.root=Path(root);self.created=0;self.inputs=[]
    def close(self):pass
    def list(self):return self.store.list('production_task')
    def get(self,id):return self.store.get('production_task',id)
    def artifact(self,id,name):
        path=self.root/id/name
        if not path.is_file():raise GeoError('ARTIFACT_MISSING','missing fixture artifact',404)
        return path
    def create(self,body):
        self.created+=1;self.inputs.append(copy.deepcopy(body));id='prod_native_'+str(self.created);p=native_plan(body);folder=self.root/id;folder.mkdir(parents=True,exist_ok=True)
        for n in ('overlay.png','analysis.tif','result.json'):(folder/n).write_bytes(b'unit-test-only')
        result={'summary':{'sampling':'native','valid_count':200,'native_pixel_count':256,'mean_ndvi':.5,'selected_count':12},'provenance':{'source_type':'public_satellite_pixels','algorithm_version':NATIVE_VERSION,'sampling':'native','scenes':['test-only']},'image':{'url':'/api/production/tasks/'+id+'/overlay.png','bounds':[113.3,30.3,113.31,30.31]}}
        t={'id':id,'status':'succeeded','progress':100,'plan':p,'result':result,'nodes':[{'id':'read','name':'native fixture read','status':'succeeded','input_summary':{'sampling':'native'}}],'created_at':str(time.time_ns())};self.store.save('production_task',t);return t
    def cancel(self,id):t=self.get(id);t['status']='cancelled';self.store.save('production_task',t);return t


class ExecutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.w=Workspace(Path(self.tmp.name)/'workspace.sqlite3');self.lc=ControlledLC(self.w,Path(self.tmp.name)/'lc');self.prod=FakeProduction(self.w,Path(self.tmp.name)/'prod');self.w.landcover=self.lc;self.w.production=self.prod
        self.patch_native=patch('execution_services.make_plan',side_effect=native_plan);self.patch_native.start()
        self.patch_boundary=patch('execution_services.load_boundary',return_value=({}, {'sha256':'b'*64,'bbox':[112.9,30.1,113.8,30.5]}));self.patch_boundary.start()
    def tearDown(self):self.w.close();self.patch_native.stop();self.patch_boundary.stop();self.tmp.cleanup()
    def run_job(self,question,context=None,**kw):return wait_job(self.w.executions,self.w.executions.create({'question':question,'context':context or {},**kw})['id'])
    def test_cold_area_invokes_real_service_and_waits_no_early_fake_success(self):
        self.lc.gate=threading.Event();j=self.w.executions.create({'question':'仙桃农田有多少亩'})
        for _ in range(100):
            current=self.w.executions.get(j['id'])
            if current.get('binding'):break
            time.sleep(.01)
        self.assertEqual(current['status'],'running');self.assertIsNone(current['result']);self.assertEqual(self.lc.created,1);self.lc.gate.set();done=wait_job(self.w.executions,j['id']);self.assertEqual(done['status'],'succeeded',done.get('error'));self.assertEqual(done['binding']['type'],'landcover');self.assertEqual(done['tool_calls'][0]['tool_id'],'landcover_area');self.assertTrue(done['tool_calls'][0]['finished_at'])
    def test_native_range_continuation_runs_without_resetting_original_time(self):
        first=self.run_job('分析这块地2025年10月和11月NDVI变化');self.assertEqual(first['status'],'needs_data');self.assertEqual(first['clarification']['missing_fields'],['bbox'])
        continued=self.w.executions.continue_job(first['id'],{'context':{'bbox':[113.3,30.3,113.31,30.31]},'request_id':'continued'});done=wait_job(self.w.executions,continued['id']);self.assertEqual(done['status'],'succeeded',done.get('error'));self.assertEqual(done['parent_execution_id'],first['id']);self.assertEqual(self.prod.inputs[0]['sampling'],'native');self.assertEqual(done['plan_snapshot']['source_snapshot']['periods']['after'],['2025-11-01','2025-11-30']);self.assertEqual(done['result_ref']['image_kind'],'change')
    def test_missing_range_and_time_are_collected_once(self):
        j=self.run_job('分析这块地NDVI变化');self.assertEqual(j['status'],'needs_data');self.assertEqual(set(j['clarification']['missing_fields']),{'bbox','periods'});self.assertEqual(self.prod.created,0)
    def test_two_month_change_requires_year_or_complete_structured_periods(self):
        bbox=[113.3,30.3,113.31,30.31];question='比较选区10月和11月NDVI变化'
        missing=self.w.executions.prepare(question,{'bbox':bbox});self.assertFalse(missing['executable']);self.assertEqual(missing['missing_fields'],['periods']);self.assertIsNone(missing['source_snapshot'])
        dated=self.w.executions.prepare('比较选区2025年10月和11月NDVI变化',{'bbox':bbox});self.assertTrue(dated['executable']);self.assertEqual(dated['tool']['inputs']['sampling'],'native')
        supplied=self.w.executions.prepare(question,{'bbox':bbox,'periods':{'before':['2025-10-01','2025-10-31'],'after':['2025-11-01','2025-11-30']}});self.assertTrue(supplied['executable']);self.assertEqual(self.prod.created,0)
    def test_explicit_single_month_or_year_cannot_be_overridden_by_context(self):
        bbox=[113.3,30.3,113.31,30.31]
        cases=[
            ('分析选中区域2025年11月NDVI',{'period':{'start':'2025-10-01','end':'2025-10-31'}}),
            ('分析选中区域2025年11月NDVI',{'periods':{'before':['2025-09-01','2025-09-30'],'after':['2025-10-01','2025-10-31']}}),
            ('分析选中区域2025年NDVI',{'period':{'start':'2026-10-01','end':'2026-10-31'}}),
            ('分析选中区域2025-11-01至2025-11-30的NDVI',{'period':{'start':'2025-10-01','end':'2025-10-31'}}),
        ]
        for question,extra in cases:
            with self.subTest(question=question,context=extra):
                j=self.run_job(question,{'bbox':bbox,**extra});self.assertEqual(j['status'],'needs_data');self.assertIn('PERIOD_CONFLICT',' '.join(j['plan_snapshot']['blocking_gaps']));self.assertIsNone(j['binding'])
        self.assertEqual(self.prod.created,0)
        good=self.run_job('分析选中区域2025年11月NDVI',{'bbox':bbox,'period':{'start':'2025-11-01','end':'2025-11-30'}})
        self.assertEqual(good['status'],'succeeded',good.get('error'));self.assertEqual(self.prod.inputs[0]['periods']['after'],['2025-11-01','2025-11-30'])
    def test_explicit_two_periods_cannot_be_replaced_and_matching_periods_run(self):
        bbox=[113.3,30.3,113.31,30.31];october=['2025-10-01','2025-10-31'];november=['2025-11-01','2025-11-30'];december=['2025-12-01','2025-12-31']
        cases=[
            ('对比选中区域2025年10月和11月NDVI变化',{'before':october,'after':december}),
            ('比较选区2025和2026年的NDVI变化',{'before':october,'after':november}),
            ('分析选区2025-10-01至2025-10-31和2025-11-01至2025-11-30的NDVI变化',{'before':october,'after':december}),
        ]
        for question,periods in cases:
            with self.subTest(question=question):
                p=self.w.executions.prepare(question,{'bbox':bbox,'periods':periods});self.assertFalse(p['executable']);self.assertIn('PERIOD_CONFLICT',' '.join(p['blocking_gaps']))
        self.assertEqual(self.prod.created,0)
        good=self.run_job('对比选中区域2025年10月和11月NDVI变化',{'bbox':bbox,'periods':{'before':october,'after':november}})
        self.assertEqual(good['status'],'succeeded',good.get('error'));self.assertEqual(self.prod.inputs[0]['periods'],{'before':october,'after':november})
    def test_idempotency_same_key_returns_same_job_and_conflict_is_409(self):
        a=self.w.executions.create({'question':'分析NDVI变化','request_id':'one'});b=self.w.executions.create({'question':'分析NDVI变化','request_id':'one'});self.assertEqual(a['id'],b['id'])
        with self.assertRaises(GeoError) as error:self.w.executions.create({'question':'仙桃农田有多少亩','request_id':'one'})
        self.assertEqual(error.exception.status,409)
    def test_cached_task_reuse_and_explicit_recompute_are_distinct(self):
        self.lc.seed('cached');a=self.run_job('仙桃农田有多少亩');self.assertTrue(a['binding']['reused']);self.assertEqual(self.lc.created,0)
        b=self.run_job('重新核算仙桃2021年面积基准');self.assertEqual(b['status'],'succeeded');self.assertFalse(b['binding']['reused']);self.assertEqual(self.lc.created,1)
    def test_legacy_representative_task_is_not_reused_as_native(self):
        body={'requirement':'2025年10月NDVI','mode':'sentinel_cog','sampling':'native','bbox':[113.3,30.3,113.31,30.31],'goal':'ndvi','periods':{'before':['2025-09-30','2025-09-30'],'after':['2025-10-01','2025-10-31']}}
        old=self.prod.create(body);old['plan']['sampling']='representative';old['result']['summary']['sampling']='representative';self.w.save('production_task',old)
        j=self.run_job('2025年10月NDVI',{'bbox':body['bbox']});self.assertEqual(j['status'],'succeeded');self.assertEqual(self.prod.created,2);self.assertNotEqual(j['binding']['task_id'],old['id'])
    def test_reference_sources_and_download_keep_original_native_binding(self):
        original=self.run_job('分析2025年10月NDVI',{'bbox':[113.3,30.3,113.31,30.31]});self.assertEqual(original['status'],'succeeded')
        for question in ('这次NDVI结果的数据来源是什么','这次NDVI结果质量如何','下载这次结果'):
            follow=self.run_job(question,{'reference_execution_id':original['id']});self.assertEqual(follow['status'],'succeeded',follow.get('error'));self.assertEqual(follow['binding']['task_id'],original['binding']['task_id']);self.assertEqual(follow['tool_calls'][0]['tool_id'],'result_reference');self.assertEqual(follow['result_ref']['map_effect'],'none')
        self.assertEqual(self.prod.created,1);self.assertEqual(self.lc.created,0)
        bad=self.run_job('这些数据准吗',{'reference_execution_id':'missing'});self.assertEqual(bad['status'],'needs_data');self.assertIsNone(bad['result'])
    def test_same_native_inputs_reuse_only_complete_native_result(self):
        context={'bbox':[113.3,30.3,113.31,30.31]}
        first=self.run_job('分析2025年10月NDVI',context);again=self.run_job('分析2025年10月NDVI',context)
        self.assertEqual(again['status'],'succeeded');self.assertEqual(self.prod.created,1);self.assertEqual(first['binding']['task_id'],again['binding']['task_id']);self.assertTrue(again['binding']['reused'])
        path=self.prod.artifact(first['binding']['task_id'],'analysis.tif');path.unlink();after=self.run_job('分析2025年10月NDVI',context)
        self.assertEqual(after['status'],'succeeded');self.assertEqual(self.prod.created,2)
    def test_old_native_algorithm_version_is_not_reused(self):
        context={'bbox':[113.3,30.3,113.31,30.31]}
        first=self.run_job('分析2025年10月NDVI',context)
        old=self.prod.get(first['binding']['task_id']);old['result']['provenance']['algorithm_version']='old-native-algorithm';self.w.save('production_task',old)
        again=self.run_job('分析2025年10月NDVI',context)
        self.assertEqual(again['status'],'succeeded');self.assertEqual(self.prod.created,2);self.assertNotEqual(again['binding']['task_id'],old['id'])
    def test_child_failure_is_failed_not_catalog_or_sample_success(self):
        def fail(body):
            task={'id':'failed-native','status':'failed','plan':native_plan(body),'error':{'code':'COG_FAILED','message':'real source unavailable'},'result':None,'created_at':'2025-01-01'};self.w.save('production_task',task);return task
        self.prod.create=fail
        job=self.run_job('分析2025年10月NDVI',{'bbox':[113.3,30.3,113.31,30.31]});self.assertEqual(job['status'],'failed');self.assertIsNone(job['result']);self.assertEqual(job['error']['code'],'CHILD_NOT_SUCCEEDED');self.assertEqual(job['tool_calls'][0]['status'],'failed')
    def test_new_analysis_with_source_phrase_does_not_read_old_result(self):
        old=self.run_job('分析2025年10月NDVI',{'bbox':[113.3,30.3,113.31,30.31]})
        job=self.run_job('请计算NDVI并说明来源',{'reference_execution_id':old['id']});self.assertEqual(job['status'],'needs_data');self.assertIsNone(job['result']);self.assertEqual(self.prod.created,1)
    def test_new_dated_analysis_with_source_phrase_does_not_use_reference_tool(self):
        bbox=[113.3,30.3,113.31,30.31];old=self.run_job('分析2025年10月NDVI',{'bbox':bbox})
        new=self.run_job('2025年10月所选区NDVI变化及来源',{'bbox':bbox,'reference_execution_id':old['id']})
        self.assertEqual(new['status'],'needs_data');self.assertEqual(new['clarification']['missing_fields'],['periods']);self.assertIsNone(new['binding']);self.assertIsNone(new['result']);self.assertEqual(self.prod.created,1)
        matched=self.run_job('分析2025年11月选区NDVI并说明来源',{'bbox':bbox,'reference_execution_id':old['id']})
        self.assertEqual(matched['status'],'succeeded',matched.get('error'));self.assertEqual(matched['tool_calls'][0]['tool_id'],'sentinel_native');self.assertNotEqual(matched['binding']['task_id'],old['binding']['task_id'])
    def test_unsupported_analysis_does_not_downgrade_to_catalog(self):
        self.w.data_catalog.search=Mock(side_effect=AssertionError('no discovery fallback'))
        for q in ['统计2026仙桃耕地面积并检索影像','检索影像并计算作物分类','计算水体变化','统计全省耕地顺便看看天气']:
            j=self.run_job(q,{'bbox':[113.3,30.3,113.31,30.31]});self.assertIn(j['status'],('unsupported','needs_data'));self.assertIsNone(j['result'])
        self.w.data_catalog.search.assert_not_called()
    def test_catalog_tool_actually_runs_but_result_stays_catalog_only(self):
        self.w.data_catalog.search=Mock(return_value={'id':'discovery1','status':'completed','results':[{'items':[{'id':'catalog-scene'}]}],'discovery_only':True})
        j=self.run_job('查询2025年10月仙桃S1卫星目录');self.assertEqual(j['status'],'succeeded',j.get('error'));self.w.data_catalog.search.assert_called_once();self.assertEqual(j['result_ref']['image_kind'],'catalog_only');self.assertTrue(j['result']['discovery_only']);self.assertEqual(self.prod.created,0)
    def test_weather_invokes_fixed_provider_and_is_not_station_measurement(self):
        self.w.public_sources.weather=Mock(return_value={'provider':'Open-Meteo','mode':'model_forecast','current':{'temperature_2m':12},'grid_location':{'latitude':30.3,'longitude':113.4}})
        j=self.run_job('仙桃今天天气');self.assertEqual(j['status'],'succeeded',j.get('error'));self.w.public_sources.weather.assert_called_once();self.assertIn('不是实测',j['summary']);self.assertEqual(j['result']['mode'],'model_forecast')
    def test_vector_algorithm_uses_user_dataset_and_rejects_synthetic_fixture(self):
        d=self.w.create_dataset({'name':'two real-input points','geojson':{'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':{'type':'Point','coordinates':[113.3,30.3]}},{'type':'Feature','properties':{},'geometry':{'type':'Point','coordinates':[113.31,30.3]}}]}})
        j=self.run_job('计算导入数据这些点的最近距离',{'dataset_id':d['id']});self.assertEqual(j['status'],'succeeded',j.get('error'));self.assertEqual(j['result']['summary']['point_count'],2);self.assertEqual(j['result_ref']['type'],'analysis')
        bad=self.run_job('统计导入数据这个图层',{'dataset_id':'xt-fixture'});self.assertEqual(bad['status'],'unsupported');self.assertIsNone(bad['result'])
    def test_landcover_question_selects_actual_classes_map_not_only_crop(self):
        self.lc.seed('cached')
        for q,code in [('仙桃水域占比多少',80),('仙桃树木覆盖面积多少',10)]:
            j=self.run_job(q);self.assertEqual(j['result_ref']['image_kind'],'classes');self.assertEqual(j['result_ref']['selected_class'],code)
        j=self.run_job('土地主要由哪些构成');self.assertEqual(j['result_ref']['image_kind'],'classes')
    def test_cancel_audit_and_success_terminal_protection(self):
        self.lc.gate=threading.Event();j=self.w.executions.create({'question':'仙桃农田有多少亩'})
        for _ in range(100):
            current=self.w.executions.get(j['id'])
            if current.get('binding'):break
            time.sleep(.01)
        cancelled=self.w.executions.cancel(j['id']);self.assertEqual(cancelled['status'],'cancelled');self.assertEqual(cancelled['tool_calls'][0]['status'],'cancelled');self.assertTrue(cancelled['tool_calls'][0]['finished_at']);self.lc.gate.set();time.sleep(.03);self.assertEqual(self.w.executions.get(j['id'])['status'],'cancelled')
    def test_retry_parent_written_before_worker_not_overwriting_terminal(self):
        old=self.run_job('NDVI分析');self.assertEqual(old['status'],'needs_data');new=self.w.executions.retry(old['id']);done=wait_job(self.w.executions,new['id']);self.assertEqual(done['parent_execution_id'],old['id']);self.assertEqual(done['status'],'needs_data');self.assertIsNotNone(done['plan_snapshot'])
    def test_job_recovery_fails_interrupted_sync_audit_without_replay(self):
        record={'id':'execution_interrupted','question':'weather','context':{},'status':'running','steps':[{'status':'running'}],'tool_calls':[{'status':'running'}],'binding':None,'created_at':'2025-01-01'};self.w.save('execution',record)
        restored=ExecutionServices(self.w).get(record['id']);self.assertEqual(restored['status'],'failed');self.assertEqual(restored['tool_calls'][0]['status'],'failed');self.assertTrue(restored['steps'][0]['finished_at'])


if __name__=='__main__':unittest.main()
