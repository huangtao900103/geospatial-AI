import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from backend.integrations.codex_gateway import CodexGateway, CodexGatewayError


SCHEMA = {"type": "object", "properties": {"answer": {"type": "string", "maxLength": 100}},
          "required": ["answer"], "additionalProperties": False}


class CodexGatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gateway-isolated-test-")
        self.root = Path(self.tmp.name)
        self.trace = self.root / "calls.jsonl"
        self.binary = self.root / "codex-fixture"
        self.binary.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, sys, time
trace=pathlib.Path(''' + repr(str(self.trace)) + ''')
with trace.open('a') as f:f.write(json.dumps({'args':sys.argv[1:],'cwd':os.getcwd()})+'\\n')
if '--version' in sys.argv:
 print('codex-cli 0.144.6');sys.exit(0)
if sys.argv[1:]==['login','status']:
 print('Logged in using ChatGPT; PRIVATE_TEST_TOKEN',file=sys.stderr);sys.exit(0)
request=json.load(sys.stdin)
time.sleep(request.get('delay',0))
if request.get('fail'):
 print('PRIVATE_TEST_TOKEN credential-error',file=sys.stderr);sys.exit(1)
output=pathlib.Path(sys.argv[sys.argv.index('-o')+1])
if not request.get('missing_output'):
 output.write_text(request.get('raw_output',json.dumps(request.get('answer',{'answer':'已收到实际结构化输入'}))),encoding='utf-8')
print(json.dumps({'type':'turn.started'}))
if request.get('unexpected_tool'):
 print(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'should never execute'}}))
if request.get('failed_event'):
 print(json.dumps({'type':'turn.failed','error':{'message':'PRIVATE_TEST_TOKEN'}}))
if not request.get('incomplete'):
 print(json.dumps({'type':'turn.completed','usage':{'input_tokens':12,'output_tokens':7}}))
''', encoding="utf-8")
        self.binary.chmod(0o700)
        self.gateway = CodexGateway(enabled=True, binary=str(self.binary), timeout=5)

    def tearDown(self):
        self.gateway.close()
        self.tmp.cleanup()

    def calls(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()] if self.trace.exists() else []

    def code(self, code, callback):
        with self.assertRaises(CodexGatewayError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("PRIVATE_TEST_TOKEN", caught.exception.message)
        return caught.exception

    def test_disabled_bridge_never_starts_a_process(self):
        bridge = CodexGateway(enabled=False, binary=str(self.binary))
        state = bridge.status()
        self.assertFalse(state["available"])
        self.code("CODEX_DISABLED", lambda: bridge.complete({}, SCHEMA))
        self.assertEqual(self.calls(), [])

    def test_status_caches_only_coarse_authentication_without_account_or_secret(self):
        state = self.gateway.status()
        self.assertTrue(state["available"])
        self.assertEqual(state["auth_method"], "chatgpt")
        self.assertEqual(state["version"], "0.144.6")
        self.assertNotIn("PRIVATE_TEST_TOKEN", json.dumps(state))
        self.gateway.status()
        self.assertEqual(len(self.calls()), 2)

    def test_success_uses_private_temp_directory_and_no_shell_or_user_tools(self):
        marker = self.root / "must-not-exist"
        result = self.gateway.complete({"question": "$(touch " + str(marker) + ")"}, SCHEMA)
        self.assertEqual(result["output"], {"answer": "已收到实际结构化输入"})
        self.assertEqual(result["metadata"]["provider"], "codex_cli")
        self.assertTrue(self.gateway.status()["inference_verified"])
        self.assertFalse(marker.exists())
        call = self.calls()[-1]
        args = call["args"]
        for flag in ["--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check", "--json"]:
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        self.assertIn("features.shell_tool=false", args)
        self.assertIn("features.apps=false", args)
        self.assertIn("features.plugins=false", args)
        self.assertNotEqual(call["cwd"], os.getcwd())
        self.assertFalse(Path(call["cwd"]).exists(), "ephemeral request directory must be removed")

    def test_schema_rejects_unknown_fields_including_nested_operation_arguments(self):
        nested = {"type": "object", "properties": {"action": {"type": "object", "properties": {"id": {"type": "string"}},
                  "required": ["id"], "additionalProperties": False}}, "required": ["action"], "additionalProperties": False}
        self.code("CODEX_INVALID_OUTPUT", lambda: self.gateway.complete({"answer": {"action": {"id": "known", "shell": "bad"}}}, nested))
        self.code("CODEX_INVALID_OUTPUT", lambda: self.gateway.complete({"answer": {"answer": "ok", "extra": "bad"}}, SCHEMA))

    def test_schema_rejects_bool_as_integer_enum_and_nonfinite_json(self):
        schema = {"type": "object", "properties": {"count": {"type": "integer", "enum": [1, 2]}}, "required": ["count"], "additionalProperties": False}
        for answer in [{"count": True}, {"count": 3}]:
            self.code("CODEX_INVALID_OUTPUT", lambda: self.gateway.complete({"answer": answer}, schema))
        self.code("CODEX_INVALID_OUTPUT", lambda: self.gateway.complete({"raw_output": '{"count":NaN}'}, schema))

    def test_schema_accepts_nullable_union_and_checks_boundaries(self):
        schema = {"type": "object", "properties": {"value": {"anyOf": [{"type": "null"}, {"type": "number", "minimum": 0, "maximum": 1}]}},
                  "required": ["value"], "additionalProperties": False}
        self.assertEqual(self.gateway.complete({"answer": {"value": None}}, schema)["output"]["value"], None)
        self.assertEqual(self.gateway.complete({"answer": {"value": 0}}, schema)["output"]["value"], 0)
        self.code("CODEX_INVALID_OUTPUT", lambda: self.gateway.complete({"answer": {"value": -1}}, schema))

    def test_partial_output_or_failed_turn_never_counts_as_success(self):
        for request, code in [({"incomplete": True}, "CODEX_INVALID_OUTPUT"),
                              ({"failed_event": True}, "CODEX_EXECUTION_FAILED"),
                              ({"missing_output": True}, "CODEX_INVALID_OUTPUT")]:
            self.code(code, lambda: self.gateway.complete(request, SCHEMA))
        self.assertFalse(self.gateway.status()["inference_verified"])

    def test_unexpected_tool_event_is_rejected(self):
        self.code("CODEX_UNEXPECTED_TOOL", lambda: self.gateway.complete({"unexpected_tool": True}, SCHEMA))

    def test_process_error_does_not_leak_raw_diagnostics(self):
        self.code("CODEX_EXECUTION_FAILED", lambda: self.gateway.complete({"fail": True}, SCHEMA))

    def test_timeout_kills_process_and_releases_the_single_request_slot(self):
        self.gateway.timeout = 0.15
        started = time.monotonic()
        self.code("CODEX_TIMEOUT", lambda: self.gateway.complete({"delay": 5}, SCHEMA))
        self.assertLess(time.monotonic() - started, 2)
        self.gateway.timeout = 5
        self.assertEqual(self.gateway.complete({}, SCHEMA)["output"]["answer"], "已收到实际结构化输入")

    def test_cancel_stops_inflight_process_and_busy_does_not_queue_duplicate_call(self):
        cancel = threading.Event()
        errors = []
        def run():
            try:
                self.gateway.complete({"delay": 10}, SCHEMA, cancel=cancel)
            except CodexGatewayError as error:
                errors.append(error.code)
        thread = threading.Thread(target=run)
        thread.start()
        for _ in range(100):
            if self.gateway._process is not None:
                break
            time.sleep(0.01)
        self.code("CODEX_BUSY", lambda: self.gateway.complete({}, SCHEMA))
        cancel.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ["CODEX_CANCELLED"])

    def test_close_stops_inflight_process_and_prevents_new_requests(self):
        errors = []
        def run():
            try:
                self.gateway.complete({"delay": 10}, SCHEMA)
            except CodexGatewayError as error:
                errors.append(error.code)
        thread = threading.Thread(target=run)
        thread.start()
        for _ in range(100):
            if self.gateway._process is not None:
                break
            time.sleep(0.01)
        self.gateway.close()
        thread.join(2)
        self.assertEqual(errors, ["CODEX_CANCELLED"])
        self.code("CODEX_DISABLED", lambda: self.gateway.complete({}, SCHEMA))

    def test_invalid_requests_never_start_inference(self):
        self.code("CODEX_INVALID_INPUT", lambda: self.gateway.complete({"invalid": float("nan")}, SCHEMA))
        self.code("CODEX_INVALID_SCHEMA", lambda: self.gateway.complete({}, {"type": "string"}))
        self.code("CODEX_INVALID_INPUT", lambda: self.gateway.complete({"question": "x" * 100000}, SCHEMA))
        self.assertEqual(self.calls(), [])

    def test_unsupported_schema_is_rejected_before_process_start(self):
        for schema in [{**SCHEMA, "allOf": [{"type": "object"}]},
                       {**SCHEMA, "properties": {"answer": {"$ref": "#/$defs/name"}}},
                       {**SCHEMA, "properties": {"answer": {"type": "string", "format": "uri"}}}]:
            self.code("CODEX_INVALID_SCHEMA", lambda: self.gateway.complete({}, schema))
        self.assertEqual(self.calls(), [])

    def test_duplicate_model_fields_and_invalid_encoding_are_rejected(self):
        self.code("CODEX_INVALID_OUTPUT", lambda: self.gateway.complete({"raw_output": '{"answer":"first","answer":"other"}'}, SCHEMA))
        self.code("CODEX_INVALID_INPUT", lambda: self.gateway.complete({"question": "\ud800"}, SCHEMA))

    def test_date_schema_and_callable_cancellation(self):
        schema = {"type": "object", "properties": {"date": {"type": "string", "format": "date"}}, "required": ["date"], "additionalProperties": False}
        self.code("CODEX_INVALID_OUTPUT", lambda: self.gateway.complete({"answer": {"date": "2026-02-30"}}, schema))
        self.assertEqual(self.gateway.complete({"answer": {"date": "2026-02-28"}}, schema)["output"]["date"], "2026-02-28")
        before = len(self.calls())
        self.code("CODEX_CANCELLED", lambda: self.gateway.complete({}, SCHEMA, cancel=lambda: True))
        self.assertEqual(len(self.calls()), before)
        self.gateway.close()
        self.assertFalse(self.gateway.status()["available"])


if __name__ == "__main__":
    unittest.main()
