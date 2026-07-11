#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llama-server OpenAI 兼容 API 客户端封装。

供 API 工作台后端代理调用 llama-server 的推理端点，避免前端跨域问题。
仅使用标准库 urllib，不引入第三方依赖。所有方法返回 (ok, data_or_error)。
"""

import json
import ssl
import urllib.error
import urllib.request


def _request(method, url, body=None, headers=None, timeout=60):
    """发送 HTTP 请求并返回 (ok, data)。
    成功时 data 为解析后的 JSON（若响应体为空则为空字符串）；
    失败时 data 为错误字符串。
    """
    h = {"Content-Type": "application/json", "User-Agent": "llama-launcher"}
    if headers:
        h.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        # HTTP 错误体里通常含 llama-server 的错误描述
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        return (False, f"HTTP {e.code}: {err_body or e.reason}")
    except urllib.error.URLError as e:
        reason = str(e.reason) if e.reason else str(e)
        return (False, f"连接失败：{reason}")
    except Exception as e:
        return (False, f"请求异常：{e}")
    if not raw:
        return (True, "")
    try:
        return (True, json.loads(raw.decode("utf-8", errors="replace")))
    except (ValueError, UnicodeDecodeError):
        return (True, raw.decode("utf-8", errors="replace"))


def base_url(host, port):
    """拼接 llama-server 的 base URL。"""
    host = (host or "127.0.0.1").strip()
    if port:
        return f"http://{host}:{int(port)}"
    return f"http://{host}"


def list_models(host, port, timeout=10):
    """GET /v1/models — 获取已加载模型列表。"""
    ok, data = _request("GET", f"{base_url(host, port)}/v1/models", timeout=timeout)
    return (ok, data)


def health(host, port, timeout=5):
    """GET /health — 探活 llama-server 推理服务是否就绪。
    返回 (ok, data)；ok=True 仅代表 HTTP 可达。
    """
    ok, data = _request("GET", f"{base_url(host, port)}/health", timeout=timeout)
    return (ok, data)


def chat_completions(host, port, payload, timeout=120):
    """POST /v1/chat/completions — 聊天补全。payload 为完整请求体 dict。"""
    ok, data = _request("POST", f"{base_url(host, port)}/v1/chat/completions",
                        body=payload, timeout=timeout)
    return (ok, data)


def embeddings(host, port, payload, timeout=60):
    """POST /v1/embeddings — 文本向量化。"""
    ok, data = _request("POST", f"{base_url(host, port)}/v1/embeddings",
                        body=payload, timeout=timeout)
    return (ok, data)


def rerank(host, port, payload, timeout=60):
    """POST /v1/rerank — 重排序。"""
    ok, data = _request("POST", f"{base_url(host, port)}/v1/rerank",
                        body=payload, timeout=timeout)
    return (ok, data)


def completions(host, port, payload, timeout=120):
    """POST /v1/completions — 文本补全（非 chat）。"""
    ok, data = _request("POST", f"{base_url(host, port)}/v1/completions",
                        body=payload, timeout=timeout)
    return (ok, data)


# -------------------- 客户端调用示例生成 --------------------

def gen_client_examples(base, model=""):
    """生成 Python / JavaScript / cURL 调用示例字符串字典。

    所有示例均指向启动器的 ``/v1/chat/completions``，并携带
    ``Authorization: Bearer sk-...`` 头（与 OpenAI 官方调用方式一致）。
    """
    model_var = model or "your-model"
    python = (
        "import requests\n\n"
        f"BASE = \"{base}\"\n"
        f"API_KEY = \"sk-your-api-key-here\"\n\n"
        f"resp = requests.post(\n"
        f"    f\"{{BASE}}/v1/chat/completions\",\n"
        f"    headers={{\"Authorization\": f\"Bearer {{API_KEY}}\"}},\n"
        "    json={\n"
        f"        \"model\": \"{model_var}\",\n"
        "        \"messages\": [\n"
        "            {\"role\": \"system\", \"content\": \"你是一个有用的助手。\"},\n"
        "            {\"role\": \"user\", \"content\": \"你好\"}\n"
        "        ],\n"
        "        \"temperature\": 0.7,\n"
        "    },\n"
        ")\n"
        "print(resp.json()[\"choices\"][0][\"message\"][\"content\"])\n"
    )
    javascript = (
        "const BASE = \"" + base + "\";\n"
        "const API_KEY = \"sk-your-api-key-here\";\n"
        "const resp = await fetch(`${BASE}/v1/chat/completions`, {\n"
        "  method: 'POST',\n"
        "  headers: {\n"
        "    'Content-Type': 'application/json',\n"
        "    'Authorization': `Bearer ${API_KEY}`\n"
        "  },\n"
        "  body: JSON.stringify({\n"
        f"    model: \"{model_var}\",\n"
        "    messages: [\n"
        "      { role: 'system', content: '你是一个有用的助手。' },\n"
        "      { role: 'user', content: '你好' }\n"
        "    ],\n"
        "    temperature: 0.7\n"
        "  })\n"
        "});\n"
        "const data = await resp.json();\n"
        "console.log(data.choices[0].message.content);\n"
    )
    curl = (
        "curl -X POST " + base + "/v1/chat/completions \\\n"
        "  -H \"Content-Type: application/json\" \\\n"
        "  -H \"Authorization: Bearer sk-your-api-key-here\" \\\n"
        "  -d '{\n"
        f"    \"model\": \"{model_var}\",\n"
        "    \"messages\": [\n"
        "      {\"role\": \"system\", \"content\": \"你是一个有用的助手。\"},\n"
        "      {\"role\": \"user\", \"content\": \"你好\"}\n"
        "    ],\n"
        "    \"temperature\": 0.7\n"
        "  }'"
    )
    return {"python": python, "javascript": javascript, "curl": curl}
