#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""api_client 单元测试（mock urllib，不依赖真实 llama-server）。

运行：python -m unittest tests.test_api_client
"""

import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import api_client


class _FakeResp:
    """模拟 urllib 的 context manager 响应。"""
    def __init__(self, data=b"", status=200, headers=None):
        self._data = data
        self.status = status
        self.headers = headers or {"Content-Length": str(len(data))}
        self._read = False

    def read(self):
        if self._read:
            return b""
        self._read = True
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _make_urlopen(data=b"", status=200, error=None):
    """构造一个 mock 的 urlopen，可注入返回数据或异常。"""
    if isinstance(data, dict):
        data = json.dumps(data).encode("utf-8")
    elif isinstance(data, str):
        data = data.encode("utf-8")

    def fake_urlopen(req, timeout=None, context=None):
        if error is not None:
            raise error
        return _FakeResp(data=data, status=status)

    return fake_urlopen


class TestBaseUrl(unittest.TestCase):
    def test_with_port(self):
        self.assertEqual(api_client.base_url("127.0.0.1", 8080),
                         "http://127.0.0.1:8080")

    def test_no_port(self):
        self.assertEqual(api_client.base_url("127.0.0.1", None),
                         "http://127.0.0.1")

    def test_empty_host_defaults(self):
        self.assertEqual(api_client.base_url("", 8080),
                         "http://127.0.0.1:8080")


class TestListModels(unittest.TestCase):
    def test_success(self):
        payload = {"data": [{"id": "model.gguf"}]}
        with mock.patch("urllib.request.urlopen", _make_urlopen(data=payload)):
            ok, data = api_client.list_models("127.0.0.1", 8080)
        self.assertTrue(ok)
        self.assertEqual(data["data"][0]["id"], "model.gguf")

    def test_http_error(self):
        import urllib.error
        err = urllib.error.HTTPError("url", 500, "Server Error",
                                     {}, io.BytesIO(b"boom"))
        with mock.patch("urllib.request.urlopen", _make_urlopen(error=err)):
            ok, data = api_client.list_models("127.0.0.1", 8080)
        self.assertFalse(ok)
        self.assertIn("HTTP 500", data)


class TestHealth(unittest.TestCase):
    def test_reachable(self):
        with mock.patch("urllib.request.urlopen", _make_urlopen(data=b'{"status":"ok"}')):
            ok, data = api_client.health("127.0.0.1", 8080)
        self.assertTrue(ok)
        self.assertEqual(data["status"], "ok")


class TestChatCompletions(unittest.TestCase):
    def test_success(self):
        payload = {"choices": [{"message": {"content": "hello"}}]}
        with mock.patch("urllib.request.urlopen", _make_urlopen(data=payload)):
            ok, data = api_client.chat_completions("127.0.0.1", 8080,
                                                   {"messages": [{"role": "user", "content": "hi"}]})
        self.assertTrue(ok)
        self.assertEqual(data["choices"][0]["message"]["content"], "hello")

    def test_url_error(self):
        import urllib.error
        err = urllib.error.URLError("connection refused")
        err.reason = "Connection refused"
        with mock.patch("urllib.request.urlopen", _make_urlopen(error=err)):
            ok, data = api_client.chat_completions("127.0.0.1", 8080, {})
        self.assertFalse(ok)
        self.assertIn("连接失败", data)


class TestEmbeddingsAndRerank(unittest.TestCase):
    def test_embeddings(self):
        payload = {"data": [{"embedding": [0.1, 0.2]}]}
        with mock.patch("urllib.request.urlopen", _make_urlopen(data=payload)):
            ok, data = api_client.embeddings("127.0.0.1", 8080, {"input": "hi"})
        self.assertTrue(ok)

    def test_rerank(self):
        payload = {"results": [{"index": 0, "relevance_score": 0.9}]}
        with mock.patch("urllib.request.urlopen", _make_urlopen(data=payload)):
            ok, data = api_client.rerank("127.0.0.1", 8080,
                                         {"query": "q", "documents": ["d"]})
        self.assertTrue(ok)


class TestGenClientExamples(unittest.TestCase):
    def test_returns_three_langs(self):
        ex = api_client.gen_client_examples("http://127.0.0.1:8080", "m.gguf")
        self.assertEqual(set(ex.keys()), {"python", "javascript", "curl"})

    def test_contains_base_url(self):
        ex = api_client.gen_client_examples("http://127.0.0.1:8080", "m.gguf")
        for lang, code in ex.items():
            self.assertIn("127.0.0.1:8080", code, f"{lang} 缺少 base url")

    def test_contains_model(self):
        ex = api_client.gen_client_examples("http://127.0.0.1:8080", "my-model.gguf")
        for lang, code in ex.items():
            self.assertIn("my-model.gguf", code, f"{lang} 缺少模型名")

    def test_default_model_when_empty(self):
        ex = api_client.gen_client_examples("http://127.0.0.1:8080", "")
        for lang, code in ex.items():
            self.assertIn("your-model", code, f"{lang} 缺少默认模型占位")

    def test_curl_has_post(self):
        ex = api_client.gen_client_examples("http://127.0.0.1:8080")
        self.assertIn("-X POST", ex["curl"])

    def test_python_has_requests(self):
        ex = api_client.gen_client_examples("http://127.0.0.1:8080")
        self.assertIn("import requests", ex["python"])


if __name__ == "__main__":
    unittest.main()
