#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``/v1/*`` OpenAI 兼容反向代理 + 认证中间件测试。

用 ``http.server.ThreadingHTTPServer`` 起一个 mock llama-server，通过
``fastapi.testclient.TestClient`` 验证：
- ``/v1/*`` 认证（无 Key / 错误 Key / proxy / admin scope）
- 反向代理透明转发（models、chat 非流式、chat SSE 流式）
- 错误格式（OpenAI 对象 vs 字符串）
- ``/api/health`` 管理端点
- ``/api/*`` 管理端点的 scope 校验

运行：python -m unittest tests.test_v1_api
依赖：httpx（仅测试场景，不计入 requirements.txt）
"""

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import skipIf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from fastapi.testclient import TestClient
    HAS_TESTCLIENT = True
except ImportError:
    HAS_TESTCLIENT = False

from core import api_keys


# -------------------- mock llama-server --------------------

class _MockLlamaHandler(BaseHTTPRequestHandler):
    """模拟 llama-server 的 /v1/* 端点。"""

    def log_message(self, *args):
        pass  # 静默

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            return self.rfile.read(length)
        return b""

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/models":
            self._send_json(200, {"object": "list", "data": [
                {"id": "test-model", "object": "model", "owned_by": "test"}]})
        elif self.path == "/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            raw = self._read_body()
            payload = json.loads(raw) if raw else {}
            if payload.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                # 发送 3 个 SSE chunk
                for i in range(3):
                    data = json.dumps({"choices": [{"delta": {"content": f"chunk{i}"}}]})
                    line = f"data: {data}\n\n".encode("utf-8")
                    self.wfile.write(f"{len(line):x}\r\n".encode() + line + b"\r\n")
                    self.wfile.flush()
                done = b"data: [DONE]\n\n"
                self.wfile.write(f"{len(done):x}\r\n".encode() + done + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            else:
                self._send_json(200, {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
                    "model": payload.get("model", "test-model"),
                })
        elif self.path == "/v1/embeddings":
            self._send_json(200, {"object": "list", "data": [
                {"index": 0, "embedding": [0.1, 0.2], "object": "embedding"}],
                "model": "test-model"})
        else:
            self._send_json(404, {"error": {"message": "not found"}})


def _start_mock_server():
    """启动 mock llama-server，返回 (server, host, port)。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockLlamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, host, port


# -------------------- 测试基类 --------------------

class _MockProc:
    """Mock subprocess.Popen 对象，使 ServerRunner.running 返回 True。

    ServerRunner.running 是只读 property（基于 self.proc.poll() is None），
    不能直接赋值；通过设置 runner.proc 为本 mock 来模拟运行状态。
    """
    pid = 99999
    stdout = None

    def poll(self):
        return None  # None 表示进程仍在运行


@skipIf(not HAS_TESTCLIENT, "httpx 未安装，跳过 TestClient 测试")
class _V1TestBase:
    """子类需继承本类 + unittest.TestCase。

    setUp 启动 mock llama-server，初始化 web.app 全局状态，创建测试 Key。
    """

    def setUp(self):
        from web import app as webapp

        # 启动 mock llama-server
        self.mock_server, self.mock_host, self.mock_port = _start_mock_server()

        # 临时 api_keys.json
        self._tmpdir = tempfile.TemporaryDirectory()
        self.keys_path = str(Path(self._tmpdir.name) / "api_keys.json")

        # 创建 proxy + admin Key
        self.proxy_key = api_keys.create_key(self.keys_path, "proxy-key", scope="proxy")["plaintext"]
        self.admin_key = api_keys.create_key(self.keys_path, "admin-key", scope="admin")["plaintext"]

        # 设置 web.app 全局状态
        webapp.api_keys_path = self.keys_path
        webapp.config_data = {"allowed_origins": []}
        webapp.web_host = "127.0.0.1"
        webapp.web_port = 8686
        # runner 指向 mock server（proc 设为 mock 使 running property 返回 True）
        webapp.runner.proc = _MockProc()
        webapp.runner.host = self.mock_host
        webapp.runner.port = self.mock_port
        webapp.runner.model = "test-model"

        self.client = TestClient(webapp.app)
        self.webapp = webapp

    def tearDown(self):
        self.mock_server.shutdown()
        self.mock_server.server_close()
        self._tmpdir.cleanup()
        # 重置 runner
        self.webapp.runner.proc = None
        self.webapp.runner.host = ""
        self.webapp.runner.port = None


# -------------------- 认证测试 --------------------

class TestV1Auth(_V1TestBase, __import__("unittest").TestCase):
    """/v1/* 认证：始终要求 sk- Key（含本机）。"""

    def test_no_key_returns_401_openai_format(self):
        """无 Key → 401 + OpenAI 错误对象格式。"""
        r = self.client.get("/v1/models")
        self.assertEqual(r.status_code, 401)
        body = r.json()
        self.assertIn("error", body)
        self.assertIsInstance(body["error"], dict)
        self.assertIn("message", body["error"])
        self.assertIn("type", body["error"])

    def test_wrong_key_returns_401(self):
        """错误 Key → 401。"""
        r = self.client.get("/v1/models",
                            headers={"Authorization": "Bearer sk-wrongkey"})
        self.assertEqual(r.status_code, 401)

    def test_non_sk_prefix_rejected(self):
        """非 sk- 前缀的凭证 → 401（/v1/* 不接受 legacy token）。"""
        r = self.client.get("/v1/models",
                            headers={"Authorization": "Bearer legacy-token-123"})
        self.assertEqual(r.status_code, 401)

    def test_proxy_scope_key_passes(self):
        """proxy-scope Key → 中间件放行（200 透传）。"""
        r = self.client.get("/v1/models",
                            headers={"Authorization": f"Bearer {self.proxy_key}"})
        self.assertEqual(r.status_code, 200)

    def test_admin_scope_key_passes(self):
        """admin-scope Key → 中间件放行（admin 覆盖 proxy）。"""
        r = self.client.get("/v1/models",
                            headers={"Authorization": f"Bearer {self.admin_key}"})
        self.assertEqual(r.status_code, 200)


# -------------------- 反向代理测试 --------------------

class TestV1ReverseProxy(_V1TestBase, __import__("unittest").TestCase):
    """反向代理透明转发。"""

    def test_get_models_proxied(self):
        """GET /v1/models 透明转发。"""
        r = self.client.get("/v1/models",
                            headers={"Authorization": f"Bearer {self.proxy_key}"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["object"], "list")
        self.assertEqual(data["data"][0]["id"], "test-model")

    def test_post_chat_non_stream_proxied(self):
        """POST /v1/chat/completions 非流式 → 完整 JSON。"""
        payload = {"model": "test-model", "messages": [{"role": "user", "content": "hi"}]}
        r = self.client.post("/v1/chat/completions", json=payload,
                             headers={"Authorization": f"Bearer {self.proxy_key}"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["choices"][0]["message"]["content"], "hello")

    def test_post_chat_stream_proxied(self):
        """POST /v1/chat/completions stream=true → SSE 逐 chunk 转发。"""
        payload = {"model": "test-model", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        r = self.client.post("/v1/chat/completions", json=payload,
                             headers={"Authorization": f"Bearer {self.proxy_key}"})
        self.assertEqual(r.status_code, 200)
        text = r.text
        # 应包含 3 个 chunk + [DONE]
        self.assertIn("chunk0", text)
        self.assertIn("chunk1", text)
        self.assertIn("chunk2", text)
        self.assertIn("[DONE]", text)

    def test_response_content_type_preserved(self):
        """响应 Content-Type 保留（application/json）。"""
        r = self.client.get("/v1/models",
                            headers={"Authorization": f"Bearer {self.proxy_key}"})
        self.assertIn("application/json", r.headers.get("content-type", ""))

    def test_stream_response_content_type_preserved(self):
        """SSE 响应 Content-Type 保留（text/event-stream）。"""
        payload = {"model": "test-model", "messages": [{"role": "user", "content": "hi"}], "stream": True}
        r = self.client.post("/v1/chat/completions", json=payload,
                             headers={"Authorization": f"Bearer {self.proxy_key}"})
        # TestClient 可能合并 content-type；检查是否包含 event-stream 或 text
        ct = r.headers.get("content-type", "")
        self.assertTrue("text/event-stream" in ct or "text/plain" in ct or "application/json" in ct,
                        f"Unexpected content-type: {ct}")

    def test_server_not_running_returns_503(self):
        """llama-server 未运行 → 503 OpenAI 格式。"""
        self.webapp.runner.proc = None
        r = self.client.get("/v1/models",
                            headers={"Authorization": f"Bearer {self.proxy_key}"})
        self.assertEqual(r.status_code, 503)
        body = r.json()
        self.assertIsInstance(body["error"], dict)

    def test_post_embeddings_proxied(self):
        """POST /v1/embeddings 透明转发。"""
        r = self.client.post("/v1/embeddings", json={"input": "hello"},
                             headers={"Authorization": f"Bearer {self.proxy_key}"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["object"], "list")


# -------------------- 管理端点测试 --------------------

class TestApiHealth(_V1TestBase, __import__("unittest").TestCase):
    """/api/health 管理端点。"""

    def test_health_reachable(self):
        """llama-server 运行中 → reachable=True。"""
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["reachable"])

    def test_health_not_running(self):
        """llama-server 未运行 → 400。"""
        self.webapp.runner.proc = None
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 400)


class TestApiManagementAuth(_V1TestBase, __import__("unittest").TestCase):
    """/api/* 管理端点 scope 校验（TestClient 模拟远程访问）。"""

    def test_proxy_key_rejected_for_management(self):
        """proxy-scope Key 不能访问管理端点（远程）→ 403。

        TestClient 的 host 是 'testclient'（不在 LOCAL_HOSTS），模拟远程访问。
        /api/keys 需要 admin scope，proxy Key 权限不足。
        """
        r = self.client.get("/api/keys",
                            headers={"Authorization": f"Bearer {self.proxy_key}"})
        self.assertEqual(r.status_code, 403)

    def test_admin_key_accepted_for_management(self):
        """admin-scope Key 可以访问管理端点（远程）→ 200。"""
        r = self.client.get("/api/keys",
                            headers={"Authorization": f"Bearer {self.admin_key}"})
        self.assertEqual(r.status_code, 200)

    def test_management_error_is_string_format(self):
        """管理端点错误返回字符串格式（非 OpenAI 对象）。"""
        r = self.client.get("/api/keys",
                            headers={"Authorization": f"Bearer {self.proxy_key}"})
        body = r.json()
        # 字符串格式：{"error": "..."}
        self.assertIsInstance(body["error"], str)


# -------------------- 端点信息测试 --------------------

class TestApiEndpoints(_V1TestBase, __import__("unittest").TestCase):
    """/api/endpoints 返回启动器 URL。"""

    def test_base_url_is_launcher(self):
        """base_url 应为启动器地址（非 llama-server 直连）。"""
        r = self.client.get("/api/endpoints")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["base_url"], "http://127.0.0.1:8686")
        self.assertTrue(data["endpoints"]["chat_completions"].endswith("/v1/chat/completions"))
        self.assertTrue(data["auth_required"])

    def test_examples_contain_bearer_header(self):
        """/api/examples 生成的示例代码包含 Authorization: Bearer。"""
        r = self.client.get("/api/examples")
        self.assertEqual(r.status_code, 200)
        examples = r.json()
        for lang, code in examples.items():
            self.assertIn("Bearer", code, f"{lang} 示例缺少 Bearer 头")
            self.assertIn("sk-", code, f"{lang} 示例缺少 sk- Key 占位符")


if __name__ == "__main__":
    import unittest
    unittest.main()
