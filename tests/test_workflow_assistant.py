"""Map-question to verified chain integration, with isolated real raster compute."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from backend.server import Workspace, LocalServer, make_handler
from backend.core.geo_services import GeoError
from backend.workflows.workflow_intents import prepare_workflow_question
from scripts.verification.workflow_fixture import seed_spectral_fixture


class WorkflowQuestionTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.w=Workspace(Path(self.tmp.name)/'workspace.sqlite3')
        self.source=seed_spectral_fixture(self.w)
        self.context={'source_type':'production','source_task_id':self.source['id']}
        self.text='对所选成果计算NDVI并执行Otsu自动阈值分区'
    def tearDown(self):self.w.close();self.tmp.cleanup()
    def wait(self,question):
        for _ in range(400):
            question=self.w.assistant.get(question['id'])
            if question['status']!='processing':return question
            time.sleep(.01)
        self.fail('isolated workflow did not settle')
    def ask(self,text=None,context=None):
        return self.wait(self.w.assistant.create({'question':text or self.text,'context':self.context if context is None else context}))
    def test_question_to_actual_raster_partition_and_evidence(self):
        self.w.model_gateway.enabled=True
        with patch.object(self.w.model_planner,'plan',side_effect=AssertionError('bounded workflow must not need model')):
            q=self.ask()
        self.assertEqual(q['status'],'answered',q)
        answer=q['answer'];self.assertNotIn('execution_ref',answer)
        self.assertEqual(answer['result_ref']['type'],'raster_processing')
        self.assertEqual(q['workflow_ref'],answer['workflow_ref'])
        workflow=self.w.analysis_workflows.get(q['workflow_ref']['id'])
        self.assertEqual([n['status'] for n in workflow['nodes']],['succeeded','succeeded'])
        self.assertEqual(workflow['result']['summary']['selected_area_ha'],5.12)
        self.assertEqual(len(workflow['evidence']),2)
        self.assertIn('不是水体、农田或灾损认定',answer['summary'])
        self.assertTrue(any(d['type']=='zip' for d in answer['downloads']))
        original=self.w.assistant.get(q['id'])['answer_snapshot']
        self.assertEqual(original,answer)
    def test_named_constraints_do_not_get_dropped_by_compound_request(self):
        for text,extra in [
            ('对所选成果计算NDMI并执行Otsu自动阈值分区',{}),
            ('对所选成果计算Landsat NDVI并执行Otsu自动阈值分区',{}),
            ('对所选成果计算NDVI并执行Otsu自动阈值分区，云量低于10',{}),
            ('对所选成果计算2026年NDVI并执行Otsu自动阈值分区',{}),
            ('计算所选农田NDVI并执行Otsu自动阈值分区',{}),
            ('对所选成果计算NDVI并执行Otsu自动阈值分区，阈值0.3',{}),
            (self.text,{'dataset_id':'other'})]:
            q=self.ask(text,{**self.context,**extra})
            self.assertEqual(q['status'],'needs_data',q)
            self.assertIsNone(q['answer'])
        self.assertEqual(self.w.analysis_workflows.list(),[])
    def test_explicit_high_direction_is_not_misread_as_low_direction(self):
        q=self.ask(self.text+'，保留不低于自动阈值的分区',{**self.context,'direction':'above'})
        self.assertEqual(q['status'],'answered',q)
        workflow=self.w.analysis_workflows.get(q['workflow_ref']['id'])
        self.assertEqual(workflow['result']['summary']['direction'],'above')
    def test_missing_observation_never_creates_computation_or_fake_map(self):
        q=self.ask(context={})
        self.assertEqual(q['status'],'needs_data',q)
        self.assertIsNone(q['answer'])
        self.assertFalse(self.w.raster_processing.list())
    def test_followup_reuses_frozen_workflow_and_shortcut_is_executable(self):
        q=self.ask();self.assertEqual(q['status'],'answered',q)
        count=len(self.w.raster_processing.list())
        for text in ('这些数据来自哪里','下载这次分析结果'):
            follow=self.ask(text,{'reference_question_id':q['id']})
            self.assertEqual(follow['status'],'answered',follow)
            self.assertEqual(follow['answer']['map_effect'],'none')
            self.assertEqual(follow['workflow_ref'],q['workflow_ref'])
            self.assertEqual(follow['answer']['result_ref'],q['answer']['result_ref'])
        self.assertEqual(len(self.w.raster_processing.list()),count)
        shortcut=self.w.assistant.save_shortcut({'name':'光谱分区','source_question_id':q['id']})
        self.assertEqual(shortcut['context'],self.context)
    def test_followup_refuses_new_source_extent_or_unfinished_question(self):
        q=self.ask();self.assertEqual(q['status'],'answered',q)
        for extra in ({'source_type':'production','source_task_id':'another'}, {'bbox':[1,1,2,2]}, {'period':{'start':'2026-01-01','end':'2026-01-31'}}):
            follow=self.ask('下载这次分析结果',{'reference_question_id':q['id'],**extra})
            self.assertEqual(follow['status'],'needs_data',follow)
            self.assertIsNone(follow['answer'])
        with patch.object(self.w.assistant,'launch'):
            pending=self.w.assistant.create({'question':self.text,'context':self.context},workflow_ref=q['workflow_ref'])
        follow=self.ask('下载这次分析结果',{'reference_question_id':pending['id']})
        self.assertEqual(follow['status'],'needs_data',follow)
        self.w.assistant.cancel(pending['id'])
    def test_followup_text_cannot_silently_change_observation_dates(self):
        q=self.ask();self.assertEqual(q['status'],'answered',q)
        for text in ('下载2026年Otsu分析结果','下载2025年10月Otsu分析结果','下载10月Otsu分析结果','下载2025-10 Otsu分析结果','下载2025-11-15的Otsu分析结果','下载2025-11-01至2025-11-40的Otsu分析结果'):
            follow=self.ask(text,{'reference_question_id':q['id']})
            self.assertEqual(follow['status'],'needs_data',follow)
            self.assertIsNone(follow['answer'])
        matched=self.ask('下载2025年11月Otsu分析结果',{'reference_question_id':q['id']})
        self.assertEqual(matched['status'],'answered',matched)
    def test_retry_answer_failure_reuses_already_successful_workflow(self):
        q=self.ask();self.assertEqual(q['status'],'answered',q)
        q.update(status='failed',answer=None,answer_snapshot=None)
        self.w.save('assistant_question',q)
        count=len(self.w.analysis_workflows.list())
        repaired=self.wait(self.w.assistant.retry(q['id']))
        self.assertEqual(repaired['status'],'answered',repaired)
        self.assertEqual(repaired['workflow_ref'],q['workflow_ref'])
        self.assertEqual(len(self.w.analysis_workflows.list()),count)
    def test_cancel_releases_workspace_lock_before_child_assignment_lock(self):
        with patch.object(self.w.assistant,'launch'):
            q=self.w.assistant.create({'question':self.text,'context':self.context},workflow_ref={'id':'test-workflow','url':'/api/analysis-workflows/test-workflow'})
        def verify_lock_released(_):
            released=[]
            def independent_reader():
                with self.w.lock:released.append(True)
            thread=threading.Thread(target=independent_reader);thread.start();thread.join(1)
            self.assertEqual(released,[True],'cancel held workspace lock while entering child service')
        with patch.object(self.w.analysis_workflows,'cancel',side_effect=verify_lock_released):
            result=self.w.assistant.cancel(q['id'])
        self.assertEqual(result['status'],'cancelled')
    def test_retry_saved_answer_cannot_reuse_changed_successful_artifacts(self):
        q=self.ask();self.assertEqual(q['status'],'answered',q)
        q.update(status='failed',answer=None,answer_snapshot=None);self.w.save('assistant_question',q)
        (self.w.production.output_dir/self.source['id']/'overlay.png').write_bytes(b'changed isolated preview')
        repeated=self.wait(self.w.assistant.retry(q['id']))
        self.assertEqual(repeated['status'],'failed',repeated)
        self.assertIsNone(repeated['answer'])
    def test_question_retry_reuses_verified_prefix(self):
        with patch('backend.analysis.adaptive_segmentation.run_segmentation',side_effect=GeoError('TEST_IO','isolated injected IO failure',502)):
            first=self.ask()
        self.assertEqual(first['status'],'failed',first)
        original=self.w.analysis_workflows.get(first['workflow_ref']['id'])
        second=self.wait(self.w.assistant.retry(first['id']))
        self.assertEqual(second['status'],'answered',second)
        repaired=self.w.analysis_workflows.get(second['workflow_ref']['id'])
        self.assertEqual(repaired['parent_workflow_id'],original['id'])
        self.assertEqual(repaired['nodes'][0]['binding'],original['nodes'][0]['binding'])
        self.assertEqual(repaired['nodes'][0]['evidence'],original['nodes'][0]['evidence'])
        self.assertEqual(self.w.analysis_workflows.get(original['id'])['status'],'failed')
    def test_knowledge_question_is_not_dispatched_as_workflow(self):
        self.assertIsNone(prepare_workflow_question(self.w,'介绍NDVI和Otsu原理，先计算再分区',self.context))
    def test_http_workflow_contract_and_question_result(self):
        server=LocalServer(('127.0.0.1',0),make_handler(self.w));server.quiet=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            caps=json.load(urlopen(base+'/api/analysis-workflows/capabilities'))
            self.assertFalse(caps['arbitrary_code'])
            request=Request(base+'/api/assistant/questions',data=json.dumps({'question':self.text,'context':self.context}).encode(),headers={'Content-Type':'application/json'})
            q=self.wait(json.load(urlopen(request)))
            self.assertEqual(q['status'],'answered',q)
            job=json.load(urlopen(base+q['workflow_ref']['url']))
            self.assertEqual(job['status'],'succeeded')
            self.assertEqual(json.load(urlopen(base+'/api/agri-trends')),[])
        finally:server.shutdown();server.server_close();thread.join(2)
