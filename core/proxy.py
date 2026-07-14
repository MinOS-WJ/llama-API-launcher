#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""流式反向代理：将请求透明转发到 llama-server。

仅使用标准库 ``http.client``，支持 SSE 流式响应（用 ``read1`` 逐块读取，
避免 ``read`` 缓冲导致的流式卡死）。供 ``web/app.py`` 的 ``/v1/*`` 路由调用。

设计要点：
- ``open_upstream`` 返回 ``(conn, resp)``，调用方负责读取 ``resp`` 并最终关闭 ``conn``。
- 请求/响应均过滤 hop-by-hop 头；请求额外剥离 ``authorization``（不转发客户端凭证）。
- 超时 600 秒，覆盖长推理场景。
"""

import http.client

# hop-by-hop 头（RFC 7230 §6.1），代理不应转发
HOP_BY_HOP = frozenset((
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
))
# 请求中额外剥离的头：由代理自行管理或不应转发到 llama-server
STRIP_REQUEST = HOP_BY_HOP | frozenset(("host", "content-length", "authorization"))
# 响应中由 StreamingResponse 自行管理的头
STRIP_RESPONSE = HOP_BY_HOP | frozenset(("content-length",))

UPSTREAM_TIMEOUT = 600  # 10 分钟，覆盖长推理


def open_upstream(host, port, method, path, headers, body):
    """打开到 llama-server 的连接并发送请求，返回 ``(conn, resp)``。

    调用方负责读取 ``resp`` 并最终关闭 ``conn``。
    """
    conn = http.client.HTTPConnection(host, port, timeout=UPSTREAM_TIMEOUT)
    filtered = {}
    for k, v in (headers or {}).items():
        if k.lower() in STRIP_REQUEST:
            continue
        filtered[k] = v
    conn.request(method, path, body=body if body else None, headers=filtered or None)
    resp = conn.getresponse()
    return conn, resp


def filter_response_headers(headers):
    """过滤上游响应头，返回 dict（供 StreamingResponse 使用）。"""
    out = {}
    for k, v in headers:
        if k.lower() in STRIP_RESPONSE:
            continue
        out[k] = v
    return out
