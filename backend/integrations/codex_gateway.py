"""Private, bounded Codex CLI inference for the local test workspace.

The CLI owns authentication. This module never reads or copies its credentials,
and model output is data: platform services remain responsible for execution.
"""
import json
import math
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from datetime import date, datetime
from pathlib import Path


class CodexGatewayError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _error(code, message):
    raise CodexGatewayError(code, message)


def _json(value, code="CODEX_INVALID_INPUT"):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError, RecursionError):
        _error(code, "模型输入须为有限的JSON数据。")


def _equal(value, other):
    return _json(value) == _json(other)


def _check_schema(schema, depth=0):
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict) or depth > 32:
        raise ValueError("schema")
    supported = {"$schema", "$id", "title", "description", "examples", "default", "type", "properties", "required",
                 "additionalProperties", "minProperties", "maxProperties", "items", "minItems", "maxItems", "uniqueItems",
                 "minLength", "maxLength", "pattern", "format", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                 "enum", "const", "anyOf", "oneOf"}
    if set(schema) - supported:
        raise ValueError("unsupported schema keyword")
    kind = schema.get("type")
    types = kind if isinstance(kind, list) else [kind] if kind is not None else []
    if any(item not in ("object", "array", "string", "boolean", "integer", "number", "null") for item in types):
        raise ValueError("schema type")
    if "properties" in schema:
        if not isinstance(schema["properties"], dict):
            raise ValueError("schema properties")
        for child in schema["properties"].values():
            _check_schema(child, depth + 1)
    if "required" in schema and (not isinstance(schema["required"], list) or not all(isinstance(v, str) for v in schema["required"])):
        raise ValueError("schema required")
    for name in ("additionalProperties", "items"):
        if name in schema:
            _check_schema(schema[name], depth + 1)
    for name in ("anyOf", "oneOf"):
        if name in schema:
            if not isinstance(schema[name], list) or not schema[name]:
                raise ValueError("schema union")
            for child in schema[name]:
                _check_schema(child, depth + 1)
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise ValueError("schema enum")
    if "format" in schema and schema["format"] not in ("date", "date-time"):
        raise ValueError("unsupported schema format")
    if "pattern" in schema:
        re.compile(schema["pattern"])


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _validate(value, schema, path="$", depth=0):
    """Validate the deliberately small JSON Schema subset used by our plans."""
    if depth > 32:
        raise ValueError("schema nesting")
    if schema is True:
        return
    if schema is False or not isinstance(schema, dict):
        raise ValueError("schema")
    if "$ref" in schema or "$dynamicRef" in schema:
        raise ValueError("schema references are not supported")
    for union in ("anyOf", "oneOf"):
        if union in schema:
            matches = 0
            branches = schema[union]
            if not isinstance(branches, list) or not branches:
                raise ValueError("schema union")
            for branch in branches:
                try:
                    _validate(value, branch, path, depth + 1)
                    matches += 1
                except (ValueError, TypeError):
                    pass
            if not matches or (union == "oneOf" and matches != 1):
                raise ValueError(path + " union")
    if "const" in schema and not _equal(value, schema["const"]):
        raise ValueError(path + " const")
    if "enum" in schema and not any(_equal(value, item) for item in schema["enum"]):
        raise ValueError(path + " enum")
    kind = schema.get("type")
    types = kind if isinstance(kind, list) else [kind] if kind is not None else []
    checks = {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "boolean": lambda: isinstance(value, bool),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
        "null": lambda: value is None,
    }
    if types and not any(t in checks and checks[t]() for t in types):
        raise ValueError(path + " type")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list) or set(required) - set(value):
            raise ValueError(path + " required")
        extra = set(value) - set(properties)
        # Every object response is closed unless explicitly given an extra-field
        # schema. Unknown model-provided operation/argument fields never leak on.
        additional = schema.get("additionalProperties", False)
        if extra and additional is False:
            raise ValueError(path + " unknown fields")
        for key, child in value.items():
            if key in properties:
                _validate(child, properties[key], path + "." + key, depth + 1)
            elif isinstance(additional, dict):
                _validate(child, additional, path + "." + key, depth + 1)
        if len(value) < schema.get("minProperties", 0) or len(value) > schema.get("maxProperties", 10000):
            raise ValueError(path + " object size")
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 10000):
            raise ValueError(path + " array size")
        if schema.get("uniqueItems") and len({_json(item) for item in value}) != len(value):
            raise ValueError(path + " duplicates")
        if isinstance(schema.get("items"), (dict, bool)):
            for index, child in enumerate(value):
                _validate(child, schema["items"], path + "[" + str(index) + "]", depth + 1)
    elif isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 200000):
            raise ValueError(path + " string size")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            raise ValueError(path + " pattern")
        if schema.get("format") == "date":
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError(path + " date")
            date.fromisoformat(value)
        elif schema.get("format") == "date-time":
            if "T" not in value:
                raise ValueError(path + " datetime")
            datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError(path + " finite")
        for key, bad in (("minimum", lambda n: value < n), ("maximum", lambda n: value > n),
                         ("exclusiveMinimum", lambda n: value <= n), ("exclusiveMaximum", lambda n: value >= n)):
            if key in schema and bad(schema[key]):
                raise ValueError(path + " number bounds")


class CodexGateway:
    def __init__(self, enabled=False, model=None, timeout=90, binary=None, concurrency=1):
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        if model is not None and (not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,95}", model)):
            raise ValueError("invalid server-side model identifier")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 1 <= timeout <= 300:
            raise ValueError("timeout must be 1 to 300 seconds")
        if concurrency != 1:
            raise ValueError("the local test bridge permits one inference at a time")
        self.enabled, self.model, self.timeout = enabled, model, float(timeout)
        self.binary = binary or "codex"
        self._gate = threading.BoundedSemaphore(1)
        self._status_lock = threading.RLock()
        self._process_lock = threading.RLock()
        self._status_cache = None
        self._status_at = 0
        self._process = None
        self._closed = False
        self._inference_verified = False

    def _binary(self):
        return shutil.which(self.binary)

    def status(self, refresh=False):
        with self._status_lock:
            if not refresh and not self._closed and self._status_cache and time.monotonic() - self._status_at < 30:
                return dict(self._status_cache, inference_verified=self._inference_verified)
            binary = self._binary()
            state = {"provider": "codex_cli", "enabled": self.enabled and not self._closed,
                     "installed": bool(binary), "authenticated": False, "available": False,
                     "auth_method": None, "version": None, "model": self.model or "codex-default",
                     "inference_verified": self._inference_verified}
            if state["enabled"] and binary:
                try:
                    version = subprocess.run([binary, "--version"], capture_output=True, text=True,
                                             timeout=5, check=False, shell=False)
                    login = subprocess.run([binary, "login", "status"], capture_output=True, text=True,
                                           timeout=5, check=False, shell=False)
                    match = re.search(r"codex(?:-cli)?\s+([0-9]+(?:\.[0-9]+){1,3})", version.stdout or "")
                    state["version"] = match.group(1) if match else None
                    # Only coarse status is retained; no raw diagnostic text or
                    # account identifier is returned to a browser or a log.
                    auth = (login.stdout or "") + "\n" + (login.stderr or "")
                    state["authenticated"] = login.returncode == 0 and "logged in" in auth.lower()
                    state["auth_method"] = "chatgpt" if state["authenticated"] and "chatgpt" in auth.lower() else "api_key" if state["authenticated"] and "api key" in auth.lower() else "stored_login" if state["authenticated"] else None
                    state["available"] = state["authenticated"] and version.returncode == 0
                except (OSError, subprocess.TimeoutExpired, UnicodeError):
                    pass
            self._status_cache, self._status_at = dict(state), time.monotonic()
            return state

    @staticmethod
    def _cancelled(cancel):
        return bool(cancel.is_set()) if hasattr(cancel, "is_set") else bool(cancel()) if callable(cancel) else False

    @staticmethod
    def _stop(process):
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                process.terminate()
            except OSError:
                pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                try:
                    process.kill()
                except OSError:
                    pass
            process.wait(timeout=2)

    def complete(self, payload, schema, cancel=None):
        if not self.enabled or self._closed:
            _error("CODEX_DISABLED", "Codex测试接入尚未启用。")
        if self._cancelled(cancel):
            _error("CODEX_CANCELLED", "本次模型请求已取消。")
        prompt = _json(payload)
        schema_text = _json(schema, "CODEX_INVALID_SCHEMA")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            _error("CODEX_INVALID_SCHEMA", "模型输出须使用明确的对象结构。")
        try:
            _check_schema(schema)
        except (ValueError, TypeError, re.error, RecursionError):
            _error("CODEX_INVALID_SCHEMA", "模型输出结构包含不支持的约束。")
        try:
            sizes = len(prompt.encode("utf-8")), len(schema_text.encode("utf-8"))
        except UnicodeError:
            _error("CODEX_INVALID_INPUT", "模型请求包含无效文字编码。")
        if sizes[0] > 96000 or sizes[1] > 64000:
            _error("CODEX_INVALID_INPUT", "模型请求过大，请缩小问题上下文。")
        if not self._gate.acquire(blocking=False):
            _error("CODEX_BUSY", "正在处理另一项分析，请稍后再试。")
        start = time.monotonic()
        process = None
        try:
            status = self.status()
            if not status["installed"]:
                _error("CODEX_UNAVAILABLE", "本机未找到Codex命令，请检查本地安装。")
            if not status["available"]:
                _error("CODEX_NOT_LOGGED_IN", "本机Codex登录不可用，请在本机完成登录后重试。")
            with tempfile.TemporaryDirectory(prefix="geoai-codex-") as directory:
                folder = Path(directory)
                schema_path, output_path = folder / "schema.json", folder / "answer.json"
                prompt_path, events_path, errors_path = folder / "request.json", folder / "events.jsonl", folder / "errors.log"
                for path, content in ((schema_path, schema_text), (prompt_path, prompt)):
                    path.write_text(content, encoding="utf-8")
                    path.chmod(0o600)
                command = [self._binary(), "-a", "never", "exec", "--ignore-user-config", "--ignore-rules",
                           "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--json",
                           "--color", "never", "--output-schema", str(schema_path), "-o", str(output_path), "-C", directory]
                for feature in ("shell_tool", "unified_exec", "apps", "plugins", "hooks", "multi_agent", "memories",
                                "computer_use", "browser_use", "browser_use_external", "image_generation", "goals",
                                "code_mode", "workspace_dependencies", "shell_snapshot"):
                    command += ["-c", "features." + feature + "=false"]
                command += ["-c", 'web_search="disabled"', "-c", "project_doc_max_bytes=0"]
                if self.model:
                    command += ["--model", self.model]
                command.append("-")
                with prompt_path.open("rb") as input_file, events_path.open("wb") as events_file, errors_path.open("wb") as errors_file:
                    events_path.chmod(0o600)
                    errors_path.chmod(0o600)
                    try:
                        process = subprocess.Popen(command, stdin=input_file, stdout=events_file, stderr=errors_file,
                                                   cwd=directory, shell=False, start_new_session=True, close_fds=True)
                    except OSError:
                        _error("CODEX_UNAVAILABLE", "Codex本机进程启动失败，请检查安装与登录。")
                    with self._process_lock:
                        self._process = process
                    while process.poll() is None:
                        if self._closed or self._cancelled(cancel):
                            self._stop(process)
                            _error("CODEX_CANCELLED", "本次模型请求已取消。")
                        if time.monotonic() - start > self.timeout:
                            self._stop(process)
                            _error("CODEX_TIMEOUT", "模型响应超时，请稍后重试。")
                        if sum(path.stat().st_size for path in (events_path, errors_path)) > 4 * 1024 * 1024:
                            self._stop(process)
                            _error("CODEX_OUTPUT_LIMIT", "模型响应超出本次处理上限。")
                        time.sleep(0.05)
                if self._closed or self._cancelled(cancel):
                    _error("CODEX_CANCELLED", "本次模型请求已取消。")
                if process.returncode != 0:
                    _error("CODEX_EXECUTION_FAILED", "Codex本次调用未完成，请检查本机登录、模型访问及用量状态。")
                if events_path.stat().st_size > 4 * 1024 * 1024 or not output_path.is_file() or output_path.stat().st_size > 128000:
                    _error("CODEX_INVALID_OUTPUT", "模型未返回可验证的结构化结果。")
                completed = False
                try:
                    event_lines = events_path.read_text(encoding="utf-8").splitlines()
                except UnicodeError:
                    _error("CODEX_INVALID_OUTPUT", "模型调用记录文字编码无效。")
                for line in event_lines:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except (ValueError, TypeError):
                        _error("CODEX_INVALID_OUTPUT", "模型调用记录格式无效。")
                    if not isinstance(event, dict):
                        _error("CODEX_INVALID_OUTPUT", "模型调用记录格式无效。")
                    if event.get("type") in ("error", "turn.failed"):
                        _error("CODEX_EXECUTION_FAILED", "Codex未完成本次模型推理。")
                    if event.get("type") == "turn.completed":
                        completed = True
                    item = event.get("item") or {}
                    if isinstance(item, dict) and item.get("type") and item["type"] not in ("agent_message", "reasoning", "plan", "todo_list"):
                        _error("CODEX_UNEXPECTED_TOOL", "模型请求了测试接入范围之外的操作，已停止处理。")
                if not completed:
                    _error("CODEX_INVALID_OUTPUT", "模型推理尚未完成，未采用中间输出。")
                try:
                    result = json.loads(output_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object,
                                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
                    if not isinstance(result, dict):
                        raise ValueError("object required")
                    _validate(result, schema)
                except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
                    _error("CODEX_INVALID_OUTPUT", "模型返回不符合约定结构，未提交分析操作。")
                self._inference_verified = True
                return {"output": result, "metadata": {"provider": "codex_cli", "model": self.model or "codex-default",
                        "duration_ms": round((time.monotonic() - start) * 1000), "cli_version": status["version"]}}
        finally:
            if process is not None and process.poll() is None:
                self._stop(process)
            with self._process_lock:
                if self._process is process:
                    self._process = None
            self._gate.release()

    def close(self):
        self._closed = True
        with self._process_lock:
            if self._process is not None:
                self._stop(self._process)
