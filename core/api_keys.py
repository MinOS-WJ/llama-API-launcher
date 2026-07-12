#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""API Key 管理：生成、哈希、校验、持久化。

纯同步、零 Web 依赖（参照 ``core/profiles.py`` 风格）。
- Key 明文格式：``sk-<24 字节 urlsafe>``（与主流 OpenAI 兼容 Key 格式一致），
  仅创建时返回一次，之后只存哈希 + 前缀。
- 哈希采用 SHA-256（key 本身高熵，无需额外盐）。
- 所有写操作用模块级 ``_write_lock`` 串行化，避免并发覆盖（见 §10.2）。
- ``verify`` 命中时更新 ``last_used_at`` 并落盘（60s debounce），写失败静默忽略，
  不阻断认证。整个校验在 ``_write_lock`` 内执行，避免 lost-update。
"""

import hashlib
import secrets
import threading
import time
import uuid

from core.profiles import load_json, save_json

KEY_PREFIX = "sk-"
# 作用域等级：admin 覆盖 proxy；proxy 不覆盖 admin
SCOPE_RANK = {"admin": 2, "proxy": 1}

# 模块级写锁：保护所有对 api_keys.json 的写操作，避免并发互相覆盖
_write_lock = threading.Lock()


def generate_key():
    """生成明文 key：``sk-`` + 24 字节 urlsafe（约 32 字符，总长约 35）。"""
    return KEY_PREFIX + secrets.token_urlsafe(24)


def key_id():
    """生成 8 位短 ID：``k_`` + uuid4 前 8 hex。"""
    return "k_" + uuid.uuid4().hex[:8]


def hash_key(plaintext):
    """返回 ``sha256:<hex>`` 形式的摘要字符串。"""
    return "sha256:" + hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def prefix_of(plaintext):
    """明文前 12 字符，用于列表展示识别（不含完整 key）。"""
    return plaintext[:12]


def load_keys(path):
    """读取 JSON；非法/缺失返回 ``[]``（复用 ``load_json`` 的容错）。"""
    data = load_json(path, None)
    if isinstance(data, list):
        return [k for k in data if isinstance(k, dict)]
    return []


def save_keys(path, keys):
    """写回 JSON（UTF-8、缩进 2，复用 ``save_json``）。"""
    save_json(path, keys)


def list_keys(path):
    """返回展示用列表（剔除 hash 字段，仅展示用字段）。"""
    out = []
    for k in load_keys(path):
        out.append({
            "id": k.get("id", ""),
            "label": k.get("label", ""),
            "prefix": k.get("prefix", ""),
            "scope": k.get("scope", "admin"),
            "enabled": k.get("enabled", True),
            "created_at": k.get("created_at", 0),
            "last_used_at": k.get("last_used_at", 0),
            "expires_at": k.get("expires_at", 0),
        })
    return out


def create_key(path, label, scope="admin", expires_at=0):
    """生成并落盘一个新 key。

    返回 dict（**含明文 plaintext，仅此一次**），其余字段与存储结构一致。
    """
    plaintext = generate_key()
    entry = {
        "id": key_id(),
        "label": label,
        "prefix": prefix_of(plaintext),
        "hash": hash_key(plaintext),
        "scope": scope if scope in SCOPE_RANK else "admin",
        "enabled": True,
        "created_at": int(time.time()),
        "last_used_at": 0,
        "expires_at": int(expires_at) if expires_at else 0,
    }
    with _write_lock:
        keys = load_keys(path)
        keys.append(entry)
        save_keys(path, keys)
    # 返回含明文（仅此一次）
    result = dict(entry)
    result["plaintext"] = plaintext
    return result


def revoke_key(path, kid):
    """按 id 删除并落盘。返回 ``(ok, msg)``，msg 为 None 表示成功。"""
    with _write_lock:
        keys = load_keys(path)
        new = [k for k in keys if k.get("id") != kid]
        if len(new) == len(keys):
            return False, "Key 不存在"
        save_keys(path, new)
    return True, None


def set_enabled(path, kid, enabled):
    """启停切换并落盘。返回 ``(ok, msg)``。"""
    with _write_lock:
        keys = load_keys(path)
        for k in keys:
            if k.get("id") == kid:
                k["enabled"] = bool(enabled)
                save_keys(path, keys)
                return True, None
    return False, "Key 不存在"


def update_label(path, kid, label):
    """改标签并落盘。返回 ``(ok, msg)``。"""
    with _write_lock:
        keys = load_keys(path)
        for k in keys:
            if k.get("id") == kid:
                k["label"] = label
                save_keys(path, keys)
                return True, None
    return False, "Key 不存在"


def verify(path, plaintext, required_scope="admin"):
    """校验明文 key。返回 ``(ok, key_id_or_msg)``。

    校验顺序（见 §4.2）：
    1. 格式校验（必须 ``sk-`` 前缀）。
    2. 遍历所有 key：跳过 disabled / 已过期 / scope 不足。
    3. hash 命中 → 更新 ``last_used_at``（60s debounce）并落盘，返回成功。
    4. 全部不命中 → 返回失败提示。

    整个校验在 ``_write_lock`` 内执行：锁内重新 ``load_keys`` 再更新，
    避免另一线程在 load 与 save 之间修改文件导致 lost-update。
    60s debounce 将高频推理时的写盘频率降到每分钟一次。
    """
    if not plaintext or not plaintext.startswith(KEY_PREFIX):
        return False, "无效的 API Key 格式"
    now = int(time.time())
    req_rank = SCOPE_RANK.get(required_scope, 2)
    with _write_lock:
        keys = load_keys(path)          # 锁内重载，避免 lost-update
        for k in keys:
            if not k.get("enabled", True):
                continue
            exp = k.get("expires_at", 0)
            if exp and now > exp:
                continue
            if SCOPE_RANK.get(k.get("scope", "admin"), 2) < req_rank:
                continue
            if hash_key(plaintext) == k.get("hash"):
                # 60s debounce：减少高频推理时的磁盘写入
                if now - k.get("last_used_at", 0) >= 60:
                    k["last_used_at"] = now
                    try:
                        save_keys(path, keys)
                    except Exception:
                        pass
                return True, k.get("id", "")
    return False, "无效或已过期的 API Key"
