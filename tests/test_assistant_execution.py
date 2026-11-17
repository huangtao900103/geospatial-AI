"""Unified customer bridge contracts; controlled tools, never network evidence."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.core.geo_services import GeoError
from backend.server import Workspace
from test_assistant import wait_answer
from test_execution import ControlledLC,FakeProduction,native_plan

class CardProduction(FakeProduction):
    def create(self,body):
        t=super().create(body);r=t['result'];r['summary'].update(mean_delta=-.05,selected_area_ha=.12,analyzed_area_ha=2.,valid_ratio=200/256)
        r['provenance']['source_details']={'scenes':{'before':{'datetime':'2025-10-02T00:00:00Z'},'after':{'datetime':'2025-11-03T00:00:00Z'}}}
        r['report']={'limitations':['unit fixture only'],'findings':[]};r['artifacts']=[]
        self.store.save('production_task',t);return t

class AssistantExecutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.w=Workspace(Path(self.tmp.name)/'workspace.sqlite3')
        self.lc=ControlledLC(self.w,Path(self.tmp.name)/'lc');self.prod=CardProduction(self.w,Path(self.tmp.name)/'prod');self.w.landcover=self.lc;self.w.production=self.prod
        self.patches=[patch('backend.workflows.execution_services.make_plan',side_effect=native_plan),patch('backend.workflows.execution_services.load_boundary',return_value=({}, {'sha256':'b'*64,'bbox':[112.9,30.1,113.8,30.5]})),patch('backend.workflows.assistant_services.load_boundary',return_value=({}, {'sha256':'b'*64,'bbox':[112.9,30.1,113.8,30.5]}))]
        for p in self.patches:p.start()
    def tearDown(self):
        self.w.close()
        for p in self.patches:p.stop()
        self.tmp.cleanup()
    def ask(self,text,**kw):return wait_answer(self.w.assistant,self.w.assistant.create({'question':text,**kw})['id'])
    def test_default_bridge_waits_for_actual_child_completion(self):
        self.assertTrue(self.w.assistant.use_execution);self.lc.gate=threading.Event();q=self.w.assistant.create({'question':'仙桃农田有多少亩'})
        for _ in range(100):
            current=self.w.assistant.get(q['id'])
            if current.get('execution_ref') and self.lc.created:break
            time.sleep(.01)
        self.assertEqual(current['status'],'processing');self.assertIsNone(current['answer']);self.assertEqual(self.lc.created,1)
        self.lc.gate.set();done=wait_answer(self.w.assistant,q['id']);self.assertEqual(done['status'],'answered',done['message']);self.assertEqual(done['answer']['execution_ref'],done['execution_ref']);self.assertEqual(done['answer']['metrics'][0]['value'],100.)
    def test_clarification_preserves_new_text_dates_and_parent_lineage(self):
        first=self.ask('分析选区NDVI变化');self.assertEqual(first['status'],'needs_data');self.assertIsNone(first['answer']);self.assertEqual(set(first['clarification']['missing_fields']),{'bbox','periods'})
        follow=self.ask(first['question']+'；补充：2025年10月和11月',context={'clarification_of':first['id'],'bbox':[113.3,30.3,113.31,30.31]})
        self.assertEqual(follow['status'],'answered',follow['message']);job=self.w.executions.get(follow['execution_ref']['id']);self.assertEqual(job['parent_execution_id'],first['execution_ref']['id']);self.assertIn('2025年10月和11月',job['question']);self.assertEqual(self.prod.inputs[0]['sampling'],'native');self.assertEqual(job['plan_snapshot']['source_snapshot']['periods']['after'],['2025-11-01','2025-11-30']);self.assertEqual(follow['answer']['metrics'][2]['value'],200)
    def test_request_id_replay_and_conflict(self):
        body={'question':'分析选区NDVI变化','request_id':'repeat'};a=self.w.assistant.create(body);b=self.w.assistant.create(body);self.assertEqual(a['id'],b['id'])
        with self.assertRaises(GeoError) as e:self.w.assistant.create({**body,'question':'另一个问题'})
        self.assertEqual(e.exception.status,409)
    def test_native_followup_retains_exact_task_and_new_question_does_not_inherit(self):
        a=self.ask('比较2025年10月和11月NDVI变化',context={'bbox':[113.3,30.3,113.31,30.31]});self.assertEqual(a['status'],'answered',a['message'])
        for text in ('这些数据来源是什么','下载这次结果'):
            b=self.ask(text,context={'reference_question_id':a['id']});self.assertEqual(b['status'],'answered',b['message']);self.assertEqual(b['binding']['task_id'],a['binding']['task_id']);self.assertEqual(b['answer']['map_effect'],'none')
        self.assertEqual(self.prod.created,1)
        c=self.ask('分析选区NDVI变化');self.assertEqual(c['status'],'needs_data');self.assertIsNone(c['binding'])
        d=self.ask('这些数据准吗',context={'reference_question_id':a['id'],'source_task_id':'different'});self.assertEqual(d['status'],'needs_data');self.assertIsNone(d['answer'])
    def test_native_shortcut_preserves_inputs_without_stored_answer(self):
        q=self.ask('比较选中区域2025年10月和11月NDVI变化',context={'bbox':[113.3,30.3,113.31,30.31]});self.assertEqual(q['status'],'answered',q['message'])
        shortcut=self.w.assistant.save_shortcut({'source_question_id':q['id']});self.assertEqual(shortcut['context']['bbox'],q['context']['bbox']);self.assertNotIn('answer',shortcut)
        replay=self.ask(shortcut['question'],capability_id=shortcut['capability_id'],context=shortcut['context']);self.assertEqual(replay['status'],'answered',replay['message']);self.assertEqual(replay['binding']['task_id'],q['binding']['task_id'])
    def test_weather_card_from_typed_return_is_numeric_and_map_stays(self):
        payload={'current':{'time':'2026-11-13T10:00','temperature_2m':18.5,'precipitation':0.,'wind_speed_10m':2.4},'units':{'current':{'temperature_2m':'°C','precipitation':'mm','wind_speed_10m':'m/s'}},'provider':'unit fixture','mode':'model_forecast'}
        with patch.object(self.w.public_sources,'weather',return_value=payload) as source:
            q=self.ask('查询选区天气',context={'bbox':[113.3,30.3,113.31,30.31]})
        self.assertEqual(q['status'],'answered',q['message']);self.assertEqual(source.call_count,1);self.assertIsNone(q['answer']['result_ref']);self.assertEqual(q['answer']['map_effect'],'none');self.assertEqual(q['answer']['metrics'][0]['value'],18.5);self.assertEqual(q['answer']['metrics'][0]['unit'],'°C')
    def test_unimplemented_goal_never_gets_answer_and_help_is_real(self):
        q=self.ask('统计全市2026年高标准农田实际面积');self.assertIn(q['status'],('needs_data','unsupported'));self.assertIsNone(q['answer']);self.assertEqual(self.lc.created,0)
        help=self.ask('当前可以提供什么基线');self.assertEqual(help['status'],'answered',help['message']);self.assertIsNone(help['answer']['result_ref'])
    def test_cancel_and_retry_are_new_executions_with_same_frozen_inputs(self):
        self.lc.gate=threading.Event();q=self.w.assistant.create({'question':'仙桃农田有多少亩'})
        for _ in range(100):
            current=self.w.assistant.get(q['id'])
            if current.get('execution_ref') and self.lc.created:break
            time.sleep(.01)
        self.w.assistant.cancel(q['id']);self.assertEqual(self.w.assistant.get(q['id'])['status'],'cancelled');self.assertIsNone(self.w.assistant.get(q['id'])['answer'])
        self.lc.gate.set();again=self.w.assistant.retry(q['id']);done=wait_answer(self.w.assistant,again['id']);self.assertEqual(done['status'],'answered',done['message']);self.assertNotEqual(done['execution_ref']['id'],current['execution_ref']['id']);self.assertEqual(done['question'],q['question'])

if __name__=='__main__':unittest.main()
