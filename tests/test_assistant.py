import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.workflows.assistant_services import AssistantServices,METHOD,route
from backend.core.geo_services import GeoError
from backend.analysis.landcover_services import PRODUCT
from backend.server import Workspace


def wait_answer(service,id):
    for _ in range(300):
        q=service.get(id)
        if q['status']!='processing':return q
        time.sleep(.01)
    raise AssertionError('question did not settle')


class FakeLandcover:
    """Deterministic task/artifact fixture; no network, no production database."""
    def __init__(self,store,root):self.store=store;self.root=Path(root);self.source_dir=None;self.created=0;self.next_area=100.;self.gate=None;self.threads=[]
    def close(self):
        if self.gate:self.gate.set()
        for t in self.threads:t.join(1)
    def list(self):return self.store.list('landcover_task')
    def get(self,id):return self.store.get('landcover_task',id)
    def artifact(self,id,name):
        task=self.get(id);p=self.root/id/name
        if task['status']!='succeeded' or not p.is_file():raise GeoError('ARTIFACT_UNAVAILABLE','fixture missing',404)
        return p
    def seed(self,id,area=100.,boundary_hash='b'*64,status='succeeded'):
        folder=self.root/id;folder.mkdir(parents=True,exist_ok=True)
        for name in ('overlay.png','classes.png','classification.tif','row_counts.csv','result.json'):(folder/name).write_text('unit-test-only')
        datahash=hashlib.sha256(str(area).encode()).hexdigest()
        classes=[{'code':40,'name':'耕作地（Cropland）','area_ha':area,'area_mu':area*15,'pixel_count':100,'share':.5,'color':'#f096ff'},{'code':80,'name':'永久水体','area_ha':60.,'area_mu':900.,'pixel_count':60,'share':.3,'color':'#0064c8'},{'code':10,'name':'树木覆盖','area_ha':40.,'area_mu':600.,'pixel_count':40,'share':.2,'color':'#006400'}]
        task={'id':id,'status':status,'progress':100 if status=='succeeded' else 10,'plan':{'product':PRODUCT,'reference_year':2021},'created_at':str(time.time_ns()),'finished_at':str(time.time_ns()),'result':{'summary':{'cropland_ha':area,'cropland_mu':area*15,'total_area_ha':200.,'valid_area_ha':200.,'coverage_ratio':1.,'cropland_share':.5,'reference_year':2021,'method':METHOD},'classes':classes,'provenance':{'product':PRODUCT,'year':2021,'boundary_sha256':boundary_hash,'boundary_source':{'sha256':boundary_hash,'boundary_year_represented':2017},'mask_rule':'pixel center inside polygon, all_touched=False','statistics_resampled':False,'classified_window_sha256':datahash}}}
        self.store.save('landcover_task',task);return task
    def create(self,body):
        self.created+=1;id='lc_fake_'+str(self.created);area=self.next_area
        task={'id':id,'status':'running','progress':10,'plan':{'reference_year':2021},'result':None,'created_at':str(time.time_ns())};self.store.save('landcover_task',task)
        def finish():
            if self.gate:self.gate.wait(2)
            time.sleep(.02);self.seed(id,area)
        thread=threading.Thread(target=finish,daemon=True);self.threads.append(thread);thread.start();return task


class IntentTest(unittest.TestCase):
    def test_supported_common_questions(self):
        for question,id in [('仙桃农田有多少亩','cropland_area'),('农田分布在哪','cropland_distribution'),('土地主要由哪些构成','landcover_composition'),('水域占比','water_area'),('这些数据准吗','source_quality'),('当前支持什么能力','platform_capabilities'),('统计仙桃全市农田面积，并展示全市分布','cropland_distribution'),('永久水体面积和耕地面积分别多少','landcover_composition')]:
            self.assertEqual(route(question)[0],id,question)
    def test_unsupported_year_scope_and_complex_metrics(self):
        for q in ['2026仙桃农田面积','2025仙桃农田有多少','今年仙桃有多少耕地','当前仙桃耕地面积是多少，请说明来源','计算今年仙桃农田面积并说明来源','2017年仙桃耕地面积多少，并给出来源','最新全市农田情况','1990年仙桃农田面积','去年仙桃农田面积','本年度仙桃农田面积','现状仙桃农田面积','今日仙桃农田面积','上年度仙桃农田面积','统计湖北全省耕地','武汉市2021耕地面积','张沟镇有多少耕地','按乡镇分耕地面积','水田与旱地分别多少','仙桃官方耕地保有量多少','高标准农田面积','仙桃水稻面积多少','非农田面积','农田减少多少','十年耕地变化','仙桃农田面积排名','养殖塘面积多少']:
            self.assertEqual(route(q,'cropland_area')[1],'unsupported',q)
        for q in ['这块地有多少亩','选中地块有多大']:self.assertEqual(route(q)[1],'needs_data')
    def test_source_questions_do_not_claim_current_counts(self):
        self.assertEqual(route('当前可以提供什么基线')[0],'platform_capabilities')
        self.assertEqual(route('市界是2017年的吗')[0],'source_quality')
        for q in ['仙桃市现在可用的数据来自哪一年','数据是哪年的','来自哪里','用的什么影像']:self.assertEqual(route(q)[0],'source_quality',q)
        self.assertEqual(route('农田面积多少，来源依据是什么')[0],'cropland_area')


class QuestionTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.w=Workspace(Path(self.tmp.name)/'workspace.sqlite3');self.fake=FakeLandcover(self.w,Path(self.tmp.name)/'artifacts');self.w.landcover=self.fake
        # Preserve the V7 assistant component contract. The default live bridge
        # and its execution lifecycle are tested in test_assistant_execution.
        self.w.assistant.use_execution=False
        self.boundary_patch=patch('backend.workflows.assistant_services.load_boundary',return_value=({}, {'sha256':'b'*64}));self.boundary_patch.start()
    def tearDown(self):self.w.close();self.boundary_patch.stop();self.tmp.cleanup()
    def ask(self,question,**kw):return wait_answer(self.w.assistant,self.w.assistant.create({'question':question,**kw})['id'])
    def test_cold_start_starts_real_task_then_answer_not_fake_early(self):
        self.fake.gate=threading.Event();q=self.w.assistant.create({'question':'仙桃农田有多少亩'})
        for _ in range(100):
            current=self.w.assistant.get(q['id'])
            if current['binding']:break
            time.sleep(.01)
        self.assertEqual(current['status'],'processing');self.assertIsNone(current['answer']);self.assertEqual(self.fake.created,1)
        self.fake.gate.set();done=wait_answer(self.w.assistant,q['id']);self.assertEqual(done['status'],'answered');self.assertEqual(done['answer']['metrics'][0]['value'],100.)
        self.assertFalse(done['binding']['reused']);self.assertEqual(done['answer_snapshot'],done['answer'])
    def test_cached_followup_and_history_never_rebind(self):
        old=self.fake.seed('lc_old');first=self.ask('仙桃农田有多少亩');self.assertEqual(first['binding']['task_id'],old['id']);self.assertTrue(first['binding']['reused']);self.assertEqual(self.fake.created,0)
        self.fake.seed('lc_new',200.)
        follow=self.ask('这些数据准吗',context={'reference_question_id':first['id']});self.assertEqual(follow['binding']['task_id'],'lc_old');self.assertEqual(follow['answer']['map_effect'],'none')
        latest=self.ask('仙桃农田有多少亩');self.assertEqual(latest['binding']['task_id'],'lc_new')
        self.assertEqual(self.w.assistant.get(first['id'])['answer']['metrics'][0]['value'],100.)
    def test_conflicting_context_needs_data_no_latest_substitution(self):
        self.fake.seed('lc_old');first=self.ask('仙桃农田有多少亩');self.fake.seed('lc_new',200.)
        result=self.ask('这些数据准吗',context={'reference_question_id':first['id'],'source_task_id':'lc_new'})
        self.assertEqual(result['status'],'needs_data');self.assertIsNone(result['answer'])
    def test_changed_boundary_or_missing_artifact_invalidates_cache(self):
        self.fake.seed('old_wrong_boundary',boundary_hash='c'*64);done=self.ask('仙桃农田有多少亩');self.assertEqual(self.fake.created,1);self.assertNotEqual(done['binding']['task_id'],'old_wrong_boundary')
        path=self.fake.artifact(done['binding']['task_id'],'overlay.png');path.unlink();again=self.ask('仙桃农田有多少亩');self.assertEqual(self.fake.created,2);self.assertEqual(again['status'],'answered')
    def test_explicit_missing_old_result_does_not_rebind_latest(self):
        self.fake.seed('old');first=self.ask('仙桃农田有多少亩');self.fake.artifact('old','overlay.png').unlink();self.fake.seed('new',200.)
        done=self.ask('下载这次分析结果',context={'reference_question_id':first['id']});self.assertEqual(done['status'],'needs_data');self.assertIsNone(done['answer'])
    def test_recompute_creates_new_task_and_cache_view_does_not(self):
        self.fake.seed('existing');a=self.ask('仙桃农田有多少亩');self.assertEqual(self.fake.created,0)
        b=self.ask('重新核算仙桃2021年面积基准');self.assertEqual(self.fake.created,1);self.assertNotEqual(a['binding']['task_id'],b['binding']['task_id']);self.assertFalse(b['binding']['reused'])
    def test_concurrent_cold_questions_join_one_real_task(self):
        self.fake.gate=threading.Event();a=self.w.assistant.create({'question':'仙桃农田有多少亩'});b=self.w.assistant.create({'question':'仙桃土地主要由哪些构成'})
        time.sleep(.05);self.assertEqual(self.fake.created,1);self.fake.gate.set();x=wait_answer(self.w.assistant,a['id']);y=wait_answer(self.w.assistant,b['id']);self.assertEqual(x['binding']['task_id'],y['binding']['task_id'])
    def test_water_composition_sources_and_downloads_use_real_fields(self):
        self.fake.seed('cached');water=self.ask('仙桃水域占比多少');self.assertEqual(water['answer']['metrics'][0]['value'],60.);self.assertIn('永久水体',water['answer']['summary']);self.assertIn('养殖塘',water['answer']['warnings'][-1])
        composition=self.ask('土地主要由哪些构成');self.assertEqual(len(composition['answer']['chart']['data']),3);self.assertEqual(composition['answer']['result_ref']['image_kind'],'classes')
        quality=self.ask('这些数据准吗');self.assertIn('不代表分类精度',quality['answer']['summary']);self.assertEqual(quality['answer']['map_effect'],'none')
        download=self.ask('下载结果');self.assertTrue(all('/api/landcover/tasks/cached/' in d['url'] or d['url']=='/api/results/landcover/cached/bundle.zip' for d in download['answer']['downloads']))
    def test_bad_hint_cannot_bypass_unsupported_and_records_are_persisted(self):
        q=self.w.assistant.create({'question':'统计2026仙桃耕地面积','capability_id':'cropland_area'});self.assertEqual(q['status'],'unsupported');self.assertIsNone(q['answer']);self.assertEqual(self.fake.created,0);self.assertEqual(self.w.assistant.get(q['id'])['status'],'unsupported')
    def test_shortcut_saves_question_not_number_and_uses_same_router(self):
        self.fake.seed('cached');q=self.ask('仙桃农田有多少亩');saved=self.w.assistant.save_shortcut({'question_id':q['id']});self.assertEqual(saved['question'],q['question']);self.assertNotIn('answer',saved);self.assertNotIn('value',saved)
        rerun=self.ask(saved['question']);self.assertEqual(rerun['binding']['task_id'],'cached');self.assertEqual(self.fake.created,0)
        with self.assertRaises(GeoError):self.w.assistant.save_shortcut({'name':'fake','question':'2026仙桃农田面积','source_question_id':q['id']})
    def test_vegetation_requires_real_aoi_not_default_small_sample(self):
        q=self.ask('仙桃2025年10月和11月NDVI变化');self.assertEqual(q['status'],'needs_data');self.assertIsNone(q['answer']);self.assertEqual(self.fake.created,0)
    def test_cancel_only_this_waiter_and_preserve_shared_task(self):
        self.fake.gate=threading.Event();a=self.w.assistant.create({'question':'仙桃农田有多少亩'});time.sleep(.03);b=self.w.assistant.create({'question':'土地主要由哪些构成'});self.assertEqual(self.w.assistant.cancel(a['id'])['status'],'cancelled');self.fake.gate.set();self.assertEqual(wait_answer(self.w.assistant,b['id'])['status'],'answered');self.assertEqual(self.w.assistant.get(a['id'])['status'],'cancelled')
    def test_platform_support_answer_needs_no_raster_task(self):
        q=self.ask('当前支持什么能力');self.assertEqual(q['status'],'answered');self.assertIsNone(q['answer']['result_ref']);self.assertEqual(self.fake.created,0)


if __name__=='__main__':unittest.main()
