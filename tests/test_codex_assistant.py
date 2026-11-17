"""Local model bridge integration; mock inference, isolated actual job services."""
import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.catalog.ai_data_tools import catalog as tool_catalog
from backend.integrations.codex_gateway import CodexGatewayError
from backend.core.geo_services import GeoError
from backend.server import Workspace, local_model_config
from test_assistant_execution import CardProduction
from test_execution import ControlledLC, native_plan


def wait_answer(service, ident):
    for _ in range(500):
        question = service.get(ident)
        if question["status"] != "processing":
            return question
        time.sleep(.01)
    raise AssertionError("question did not settle")


def reply(message="NDVI用于描述植被光谱特征，不直接认定作物或产量。"):
    return {"kind": "reply", "title": "植被指数说明", "message": message,
            "provenance": {"provider": "codex_cli", "model": "isolated-test", "planner_mode": "codex_cli"}}


def execute(question, context=None):
    return {"kind": "execute", "title": "经校验的隔离测试计划", "message": "已核对分析条件",
            "execution_question": question, "execution_context": context or {},
            "provenance": {"provider": "codex_cli", "model": "isolated-test", "planner_mode": "codex_cli"}}


class MockPlanner:
    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.seen = []
        self.entered = threading.Event()
        self.release = None
        self.check_cancellation = True
        self.lock = threading.Lock()

    def plan(self, question, cancel=None):
        with self.lock:
            index = len(self.seen)
            self.seen.append(copy.deepcopy(question))
            decision = self.decisions[min(index, len(self.decisions) - 1)] if self.decisions else reply()
        self.entered.set()
        if self.release is not None:
            while not self.release.wait(.01):
                if self.check_cancellation and cancel and cancel():
                    raise CodexGatewayError("CODEX_CANCELLED", "本次模型请求已取消。")
        if isinstance(decision, Exception):
            raise decision
        return copy.deepcopy(decision)


class CodexAssistantTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="codex-assistant-isolated-")
        self.root = Path(self.tmp.name)
        self.w = Workspace(self.root / "workspace.sqlite3")
        self.lc = ControlledLC(self.w, self.root / "lc")
        self.prod = CardProduction(self.w, self.root / "prod")
        self.w.landcover, self.w.production = self.lc, self.prod
        self.w.model_gateway.enabled = True
        self.planner = MockPlanner()
        self.w.model_planner = self.planner
        self.patches = [
            patch('backend.workflows.execution_services.make_plan', side_effect=native_plan),
            patch('backend.workflows.execution_services.load_boundary', return_value=({}, {"sha256": "b" * 64, "bbox": [112.9, 30.1, 113.8, 30.5]})),
            patch('backend.workflows.assistant_services.load_boundary', return_value=({}, {"sha256": "b" * 64, "bbox": [112.9, 30.1, 113.8, 30.5]})),
            patch.object(self.w.model_gateway, "complete", side_effect=AssertionError("real model inference forbidden in integration tests")),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        if self.planner.release is not None:
            self.planner.release.set()
        self.w.close()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def set_planner(self, *decisions):
        self.planner = MockPlanner(*decisions)
        self.w.model_planner = self.planner
        return self.planner

    def ask(self, text, application_ref=None, **kwargs):
        question = self.w.assistant.create({"question": text, **kwargs}, application_ref=application_ref)
        return wait_answer(self.w.assistant, question["id"])

    def test_general_reply_has_no_map_or_algorithm_result_and_is_frozen(self):
        self.set_planner(reply())
        question = self.ask("解释一下植被指数的用途")
        self.assertEqual(question["status"], "answered", question["message"])
        self.assertIsNone(question["binding"])
        self.assertNotIn("execution_ref", question)
        self.assertEqual(self.w.executions.list(), [])
        self.assertEqual(question["answer"]["metrics"], [])
        self.assertEqual(question["answer"]["map_effect"], "none")
        self.assertIsNone(question["answer"]["result_ref"])
        self.assertEqual(question["answer"]["downloads"], [])
        self.assertEqual(question["answer"], question["answer_snapshot"])
        question["answer"]["summary"] = "must not rewrite stored history"
        self.assertIn("NDVI", self.w.assistant.get(question["id"])["answer"]["summary"])
        self.assertEqual(self.lc.created, 0)

    def test_source_word_followup_to_general_reply_stays_in_conversation(self):
        self.set_planner(reply(), reply("这是一段业务原理说明，没有对应的遥感统计成果。"))
        first = self.ask("解释一下遥感指数的用途")
        second = self.ask("这些数据来自哪里", context={"reference_question_id": first["id"]})
        self.assertEqual(second["status"], "answered", second["message"])
        self.assertEqual(len(self.planner.seen), 2)
        self.assertEqual(self.planner.seen[1]["context"]["reference_question_id"], first["id"])
        self.assertEqual(second["parent_question_id"], first["id"])
        self.assertIsNone(second["binding"])
        self.assertIsNone(second["answer"]["result_ref"])
        self.assertEqual(self.w.executions.list(), [])
        self.assertEqual(self.lc.created, 0)

    def test_validated_model_plan_executes_with_original_question_preserved(self):
        original = "帮我看看家乡田块的历史底数"
        canonical = "仙桃2021年耕作地参考面积是多少"
        self.set_planner(execute(canonical, {"region": "仙桃市"}))
        question = self.ask(original)
        self.assertEqual(question["status"], "answered", question["message"])
        self.assertEqual(question["question"], original)
        self.assertEqual(question["execution_input"]["question"], canonical)
        self.assertEqual(question["original_context"], {})
        job = self.w.executions.get(question["execution_ref"]["id"])
        self.assertEqual(job["question"], canonical)
        self.assertEqual(job["context"]["region"], "仙桃市")
        self.assertEqual(question["binding"]["type"], "landcover")
        self.assertEqual(question["answer"]["metrics"][0]["value"], 100.)
        self.assertEqual(self.lc.created, 1)
        self.assertEqual(question["answer"]["provenance"]["model_planning"]["provider"], "codex_cli")

    def test_validated_execution_takes_priority_over_initial_platform_help_intent(self):
        original = "当前可以提供什么数据基线"
        canonical = "仙桃2021年耕作地参考面积是多少"
        self.set_planner(execute(canonical, {"region": "仙桃市"}))
        question = self.ask(original)
        self.assertEqual(self.planner.seen[0]["capability_id"], "platform_capabilities")
        self.assertEqual(question["status"], "answered", question["message"])
        self.assertEqual(question["question"], original)
        self.assertEqual(question["execution_input"]["question"], canonical)
        self.assertEqual(question["binding"]["type"], "landcover")
        self.assertEqual(question["answer"]["metrics"][0]["value"], 100.)
        self.assertEqual(self.lc.created, 1)
        self.assertNotEqual(question["answer"]["title"], "当前可用的固定分析能力")

    def test_canonical_native_plan_reaches_existing_parameter_validation_and_engine(self):
        bbox = [113.3, 30.3, 113.31, 30.31]
        periods = {"before": ["2025-10-01", "2025-10-31"], "after": ["2025-11-01", "2025-11-30"]}
        canonical = "比较选中区域2025年10月和11月NDVI变化"
        self.set_planner(execute(canonical, {"bbox": bbox, "periods": periods}))
        question = self.ask("看看这里前后植被差多少", context={"bbox": bbox})
        self.assertEqual(question["status"], "answered", question["message"])
        self.assertEqual(question["original_context"], {"bbox": bbox})
        self.assertEqual(self.prod.created, 1)
        self.assertEqual(self.prod.inputs[0]["bbox"], bbox)
        self.assertEqual(self.prod.inputs[0]["periods"], periods)
        self.assertEqual(self.prod.inputs[0]["sampling"], "native")
        self.assertEqual(question["binding"]["type"], "production")

    def test_explicit_typed_form_bypasses_model(self):
        self.set_planner(AssertionError("typed forms must not invoke the language planner"))
        question = self.ask("仙桃2021年耕作地参考面积是多少", application_ref={"id": "isolated-form", "name": "隔离表单"})
        self.assertEqual(question["status"], "answered", question["message"])
        self.assertEqual(self.planner.seen, [])
        self.assertEqual(self.lc.created, 1)

    def test_real_source_and_download_followups_bypass_model_and_keep_binding(self):
        self.set_planner(AssertionError("source/download must not invoke model"))
        first = self.ask("仙桃2021年耕作地参考面积是多少", application_ref={"id": "isolated-form"})
        for text in ("这些数据来自哪里", "下载这次成果"):
            follow = self.ask(text, context={"reference_question_id": first["id"]})
            self.assertEqual(follow["status"], "answered", follow["message"])
            self.assertEqual(follow["binding"]["task_id"], first["binding"]["task_id"])
            self.assertEqual(follow["answer"]["map_effect"], "none")
        self.assertEqual(self.planner.seen, [])
        self.assertEqual(self.lc.created, 1)

    def test_model_failure_unknown_question_fails_without_invented_result(self):
        self.set_planner(CodexGatewayError("CODEX_TIMEOUT", "模型响应超时，请稍后重试。"))
        question = self.ask("解释地理空间学的基础概念")
        self.assertEqual(question["status"], "failed")
        self.assertEqual(question["model_error"]["code"], "CODEX_TIMEOUT")
        self.assertIsNone(question["answer"])
        self.assertEqual(self.w.executions.list(), [])
        self.assertEqual(self.lc.created, 0)

    def test_model_failure_known_supported_question_falls_back_to_registered_tool(self):
        self.set_planner(CodexGatewayError("CODEX_TIMEOUT", "模型响应超时，请稍后重试。"))
        question = self.ask("仙桃2021年耕作地参考面积是多少")
        self.assertEqual(question["status"], "answered", question["message"])
        self.assertEqual(question["model_error"]["code"], "CODEX_TIMEOUT")
        self.assertEqual(question["binding"]["type"], "landcover")
        self.assertEqual(self.lc.created, 1)

    def test_general_conversation_failure_does_not_fall_back_to_old_landcover(self):
        self.set_planner(reply(), CodexGatewayError("CODEX_TIMEOUT", "模型响应超时，请稍后重试。"))
        first = self.ask("解释一下遥感指数")
        follow = self.ask("这些数据来自哪里", context={"reference_question_id": first["id"]})
        self.assertEqual(follow["status"], "failed")
        self.assertEqual(self.w.executions.list(), [])
        self.assertEqual(self.lc.created, 0)

    def test_general_source_followup_with_disabled_model_never_uses_old_landcover(self):
        self.set_planner(reply())
        first = self.ask("解释一下遥感指数")
        self.w.model_gateway.enabled = False
        follow = self.ask("这些数据来自哪里", context={"reference_question_id": first["id"]})
        self.assertIn(follow["status"], ("needs_data", "failed"), follow["message"])
        self.assertIsNone(follow["answer"])
        self.assertIsNone(follow["binding"])
        self.assertEqual(self.w.executions.list(), [])
        self.assertEqual(self.lc.created, 0)

    def test_general_download_followup_with_unusable_plan_never_uses_old_landcover(self):
        self.set_planner(reply(), {"kind": "fallback", "provenance": {"provider": "codex_cli"}})
        first = self.ask("解释一下遥感指数")
        follow = self.ask("下载这次成果", context={"reference_question_id": first["id"]})
        self.assertIn(follow["status"], ("needs_data", "failed"), follow["message"])
        self.assertIsNone(follow["answer"])
        self.assertIsNone(follow["binding"])
        self.assertEqual(self.w.executions.list(), [])
        self.assertEqual(self.lc.created, 0)

    def test_idempotent_request_does_not_repeat_inference_or_change_question(self):
        planner = self.set_planner(reply())
        planner.release = threading.Event()
        body = {"question": "解释遥感时空分析", "request_id": "isolated-model-repeat"}
        first = self.w.assistant.create(body)
        self.assertTrue(planner.entered.wait(1))
        second = self.w.assistant.create(body)
        self.assertEqual(first["id"], second["id"])
        with self.assertRaises(GeoError) as caught:
            self.w.assistant.create({**body, "question": "不同的问题"})
        self.assertEqual(caught.exception.status, 409)
        planner.release.set()
        done = wait_answer(self.w.assistant, first["id"])
        self.assertEqual(done["status"], "answered")
        self.assertEqual(len(planner.seen), 1)
        self.assertEqual(self.w.assistant.create(body)["id"], first["id"])
        self.assertEqual(len(planner.seen), 1)

    def test_cancel_during_inference_preserves_cancelled_terminal_state(self):
        planner = self.set_planner(reply())
        planner.release = threading.Event()
        question = self.w.assistant.create({"question": "解释遥感时空分析"})
        self.assertTrue(planner.entered.wait(1))
        self.w.assistant.cancel(question["id"])
        planner.release.set()
        for thread in self.w.assistant.threads:
            thread.join(1)
        done = self.w.assistant.get(question["id"])
        self.assertEqual(done["status"], "cancelled")
        self.assertIsNone(done["answer"])
        self.assertEqual(self.w.executions.list(), [])
        self.assertEqual(len(planner.seen), 1)

    def test_late_model_reply_after_cancel_never_replaces_cancelled_question(self):
        planner = self.set_planner(reply())
        planner.release = threading.Event()
        planner.check_cancellation = False
        question = self.w.assistant.create({"question": "解释遥感时空分析"})
        self.assertTrue(planner.entered.wait(1))
        self.w.assistant.cancel(question["id"])
        planner.release.set()
        for thread in self.w.assistant.threads:
            thread.join(1)
        self.assertEqual(self.w.assistant.get(question["id"])["status"], "cancelled")
        self.assertIsNone(self.w.assistant.get(question["id"])["answer"])
        self.assertEqual(self.w.executions.list(), [])

    def test_model_clarification_accepts_frontend_supplement_and_keeps_parent(self):
        original = "我想对比选区植被变化"
        clarification = {"kind": "clarify", "title": "补充条件", "message": "请选择范围并明确前后观测时段。",
                         "clarification": {"missing_fields": ["bbox", "periods"], "message": "请选择范围并明确前后观测时段。"},
                         "provenance": {"provider": "codex_cli"}}
        bbox = [113.3, 30.3, 113.31, 30.31]
        periods = {"before": ["2025-10-01", "2025-10-31"], "after": ["2025-11-01", "2025-11-30"]}
        self.set_planner(clarification, execute("比较选中区域2025年10月和11月NDVI变化", {"bbox": bbox, "periods": periods}))
        first = self.ask(original)
        self.assertEqual(first["status"], "needs_data")
        self.assertIsNone(first["answer"])
        follow_text = original + "；补充：2025年10月和11月"
        follow = self.ask(follow_text, context={"clarification_of": first["id"], "bbox": bbox, "periods": periods})
        self.assertEqual(follow["status"], "answered", follow["message"])
        self.assertEqual(follow["question"], follow_text)
        self.assertEqual(follow["parent_question_id"], first["id"])
        self.assertEqual(self.planner.seen[1]["context"]["bbox"], bbox)
        self.assertEqual(self.prod.created, 1)

    def test_changed_clarification_is_rejected_before_model_or_execution(self):
        self.set_planner({"kind": "clarify", "message": "请选择范围", "provenance": {}})
        first = self.ask("解释一下业务指标")
        with self.assertRaises(GeoError) as caught:
            self.w.assistant.create({"question": "完全替换原问题", "context": {"clarification_of": first["id"]}})
        self.assertEqual(caught.exception.code, "CLARIFICATION_CHANGED")
        self.assertEqual(len(self.planner.seen), 1)
        self.assertEqual(self.w.executions.list(), [])


class CodexLocalConfigTest(unittest.TestCase):
    def test_new_workspace_disabled_and_status_never_contains_credentials(self):
        with tempfile.TemporaryDirectory(prefix="codex-default-isolated-") as directory:
            root = Path(directory)
            self.assertEqual(local_model_config(root / "absent.json"), {"enabled": False, "timeout": 180})
            workspace = Workspace(root / "workspace.sqlite3")
            try:
                self.assertFalse(workspace.model_gateway.enabled)
                with patch('backend.integrations.codex_gateway.subprocess.run', side_effect=AssertionError("disabled status must not invoke CLI")):
                    state = workspace.model_gateway.status(refresh=True)
                self.assertFalse(state["enabled"])
                self.assertFalse(state["available"])
                text = json.dumps(state).lower()
                for forbidden in ("api_key", "access_token", "refresh_token", "auth.json"):
                    self.assertNotIn(forbidden, text)
                self.assertFalse(tool_catalog(state)["model_service_configured"])
            finally:
                workspace.close()

    def test_local_config_has_only_opt_in_nonsecret_fields(self):
        with tempfile.TemporaryDirectory(prefix="codex-config-isolated-") as directory:
            path = Path(directory) / "model.json"
            path.write_text(json.dumps({"provider": "codex_cli", "enabled": True, "timeout": 180, "model": "isolated-model"}))
            self.assertEqual(local_model_config(path), {"enabled": True, "timeout": 180, "model": "isolated-model"})
            for extra in ({"api_key": "MUST_NOT_STORE"}, {"binary": "/unexpected/program"}, {"endpoint": "https://untrusted.invalid"}):
                path.write_text(json.dumps({"provider": "codex_cli", "enabled": True, **extra}))
                with self.assertRaises(ValueError):
                    local_model_config(path)


if __name__ == "__main__":
    unittest.main()
