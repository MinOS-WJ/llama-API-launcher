#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.api_keys 单元测试。

覆盖：生成、哈希、verify（命中/禁用/过期/scope/不匹配）、
create/revoke/toggle/rename、空文件与非法 JSON 容错。
运行：python -m unittest tests.test_api_keys
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import api_keys


class TestPrimitives(unittest.TestCase):
    """生成器与哈希函数。"""

    def test_generate_key_format(self):
        k = api_keys.generate_key()
        self.assertTrue(k.startswith(api_keys.KEY_PREFIX))
        # 前缀之后至少 24 字符（urlsafe 24 字节约 32 字符）
        self.assertGreaterEqual(len(k), len(api_keys.KEY_PREFIX) + 24)

    def test_generate_key_uniqueness(self):
        keys = {api_keys.generate_key() for _ in range(200)}
        self.assertEqual(len(keys), 200)

    def test_key_id_format(self):
        kid = api_keys.key_id()
        self.assertTrue(kid.startswith("k_"))
        self.assertEqual(len(kid), 2 + 8)

    def test_key_id_uniqueness(self):
        ids = {api_keys.key_id() for _ in range(200)}
        self.assertEqual(len(ids), 200)

    def test_hash_key_format(self):
        h = api_keys.hash_key("sk-test")
        self.assertTrue(h.startswith("sha256:"))
        self.assertEqual(len(h), len("sha256:") + 64)

    def test_hash_key_deterministic(self):
        self.assertEqual(api_keys.hash_key("abc"), api_keys.hash_key("abc"))
        self.assertNotEqual(api_keys.hash_key("abc"), api_keys.hash_key("abd"))

    def test_prefix_of(self):
        k = "sk-9f2aQp3xR7mZbN1vT8wYdC4hK6jLsE0u"
        self.assertEqual(api_keys.prefix_of(k), "sk-9f2aQp3xR")


class TestLoadSave(unittest.TestCase):
    """容错与往返。"""

    def test_load_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(api_keys.load_keys(str(Path(d) / "no.json")), [])

    def test_load_invalid_json_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "bad.json")
            Path(p).write_text("not json {{{")
            self.assertEqual(api_keys.load_keys(p), [])

    def test_load_non_list_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "obj.json")
            Path(p).write_text(json.dumps({"not": "a list"}))
            self.assertEqual(api_keys.load_keys(p), [])

    def test_load_filters_non_dict_entries(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "mix.json")
            api_keys.save_keys(p, [{"id": "k_1"}, "junk", 42, {"id": "k_2"}])
            keys = api_keys.load_keys(p)
            self.assertEqual(len(keys), 2)

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            data = [{"id": "k_1", "label": "测试", "hash": "sha256:abc"}]
            api_keys.save_keys(p, data)
            self.assertEqual(api_keys.load_keys(p), data)


class TestListKeys(unittest.TestCase):
    """list_keys 必须剔除 hash 字段。"""

    def test_list_keys_excludes_hash(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            api_keys.save_keys(p, [{
                "id": "k_1", "label": "a", "prefix": "sk-abcd1234",
                "hash": "sha256:secret", "scope": "admin", "enabled": True,
                "created_at": 100, "last_used_at": 0, "expires_at": 0,
            }])
            result = api_keys.list_keys(p)
            self.assertEqual(len(result), 1)
            self.assertNotIn("hash", result[0])
            self.assertNotIn("plaintext", result[0])
            self.assertEqual(result[0]["id"], "k_1")
            self.assertEqual(result[0]["label"], "a")

    def test_list_keys_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(api_keys.list_keys(str(Path(d) / "no.json")), [])


class TestCreateKey(unittest.TestCase):
    """create_key 落盘并返回明文（仅一次）。"""

    def test_create_returns_plaintext_once(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            created = api_keys.create_key(p, "开发机")
            self.assertIn("plaintext", created)
            self.assertTrue(created["plaintext"].startswith(api_keys.KEY_PREFIX))
            self.assertEqual(created["label"], "开发机")
            self.assertEqual(created["scope"], "admin")
            self.assertTrue(created["enabled"])
            self.assertNotEqual(created["created_at"], 0)
            # 落盘后不应含明文
            stored = api_keys.load_keys(p)
            self.assertEqual(len(stored), 1)
            self.assertNotIn("plaintext", stored[0])
            self.assertIn("hash", stored[0])
            self.assertEqual(stored[0]["prefix"], created["prefix"])

    def test_create_scope_validation(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            # 非法 scope 回退为 admin
            created = api_keys.create_key(p, "x", scope="bogus")
            self.assertEqual(created["scope"], "admin")
            # 合法 proxy scope
            created2 = api_keys.create_key(p, "y", scope="proxy")
            self.assertEqual(created2["scope"], "proxy")

    def test_create_multiple_appends(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            api_keys.create_key(p, "a")
            api_keys.create_key(p, "b")
            self.assertEqual(len(api_keys.load_keys(p)), 2)

    def test_create_expires_at(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            fut = int(time.time()) + 3600
            created = api_keys.create_key(p, "x", expires_at=fut)
            self.assertEqual(created["expires_at"], fut)


class TestRevoke(unittest.TestCase):
    def test_revoke_existing(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            c1 = api_keys.create_key(p, "a")
            c2 = api_keys.create_key(p, "b")
            ok, msg = api_keys.revoke_key(p, c1["id"])
            self.assertTrue(ok)
            self.assertIsNone(msg)
            remaining = api_keys.load_keys(p)
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0]["id"], c2["id"])

    def test_revoke_nonexistent(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            api_keys.create_key(p, "a")
            ok, msg = api_keys.revoke_key(p, "k_nope0000")
            self.assertFalse(ok)
            self.assertIn("不存在", msg)


class TestToggle(unittest.TestCase):
    def test_toggle_off_then_on(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            c = api_keys.create_key(p, "a")
            ok, _ = api_keys.set_enabled(p, c["id"], False)
            self.assertTrue(ok)
            keys = api_keys.load_keys(p)
            self.assertFalse(keys[0]["enabled"])
            ok, _ = api_keys.set_enabled(p, c["id"], True)
            self.assertTrue(ok)
            self.assertTrue(api_keys.load_keys(p)[0]["enabled"])

    def test_toggle_nonexistent(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            api_keys.create_key(p, "a")
            ok, msg = api_keys.set_enabled(p, "k_nope0000", False)
            self.assertFalse(ok)
            self.assertIn("不存在", msg)


class TestRename(unittest.TestCase):
    def test_rename_existing(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            c = api_keys.create_key(p, "old")
            ok, _ = api_keys.update_label(p, c["id"], "new")
            self.assertTrue(ok)
            self.assertEqual(api_keys.load_keys(p)[0]["label"], "new")

    def test_rename_nonexistent(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            api_keys.create_key(p, "a")
            ok, msg = api_keys.update_label(p, "k_nope0000", "x")
            self.assertFalse(ok)
            self.assertIn("不存在", msg)


class TestVerify(unittest.TestCase):
    """verify 校验顺序与边界。"""

    def test_verify_hit_updates_last_used(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            c = api_keys.create_key(p, "a")
            before = api_keys.load_keys(p)[0]["last_used_at"]
            self.assertEqual(before, 0)
            ok, info = api_keys.verify(p, c["plaintext"])
            self.assertTrue(ok)
            self.assertEqual(info, c["id"])
            after = api_keys.load_keys(p)[0]["last_used_at"]
            self.assertGreater(after, 0)

    def test_verify_wrong_key(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            api_keys.create_key(p, "a")
            ok, msg = api_keys.verify(p, api_keys.generate_key())
            self.assertFalse(ok)
            self.assertIn("无效", msg)

    def test_verify_disabled_key(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            c = api_keys.create_key(p, "a")
            api_keys.set_enabled(p, c["id"], False)
            ok, msg = api_keys.verify(p, c["plaintext"])
            self.assertFalse(ok)

    def test_verify_expired_key(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            past = int(time.time()) - 100
            c = api_keys.create_key(p, "a", expires_at=past)
            ok, _ = api_keys.verify(p, c["plaintext"])
            self.assertFalse(ok)

    def test_verify_non_expired_key(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            fut = int(time.time()) + 3600
            c = api_keys.create_key(p, "a", expires_at=fut)
            ok, _ = api_keys.verify(p, c["plaintext"])
            self.assertTrue(ok)

    def test_verify_scope_admin_covers_proxy(self):
        # admin key 可用于 proxy 路径
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            c = api_keys.create_key(p, "a", scope="admin")
            ok, _ = api_keys.verify(p, c["plaintext"], required_scope="proxy")
            self.assertTrue(ok)

    def test_verify_scope_proxy_not_admin(self):
        # proxy key 不可用于 admin 路径
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            c = api_keys.create_key(p, "a", scope="proxy")
            ok, _ = api_keys.verify(p, c["plaintext"], required_scope="admin")
            self.assertFalse(ok)
            # 但可用于 proxy 路径
            ok, _ = api_keys.verify(p, c["plaintext"], required_scope="proxy")
            self.assertTrue(ok)

    def test_verify_bad_format(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            api_keys.create_key(p, "a")
            ok, msg = api_keys.verify(p, "not-an-sk-key")
            self.assertFalse(ok)
            self.assertIn("格式", msg)

    def test_verify_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            api_keys.create_key(p, "a")
            ok, _ = api_keys.verify(p, "")
            self.assertFalse(ok)

    def test_verify_empty_file(self):
        # api_keys.json 不存在时 verify 应安全返回失败
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "no.json")
            ok, _ = api_keys.verify(p, api_keys.generate_key())
            self.assertFalse(ok)

    def test_verify_writes_silently_on_success(self):
        # last_used_at 落盘失败不应阻断认证（此处模拟正常路径即可）
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            c = api_keys.create_key(p, "a")
            ok, info = api_keys.verify(p, c["plaintext"])
            self.assertTrue(ok)
            self.assertEqual(info, c["id"])


class TestEndToEnd(unittest.TestCase):
    """完整 CRUD 闭环。"""

    def test_full_lifecycle(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "k.json")
            # 创建两个
            a = api_keys.create_key(p, "开发机", scope="admin")
            b = api_keys.create_key(p, "推理客户端", scope="proxy")
            self.assertEqual(len(api_keys.list_keys(p)), 2)
            # list 不含 hash/plaintext
            for item in api_keys.list_keys(p):
                self.assertNotIn("hash", item)
                self.assertNotIn("plaintext", item)
            # 两个都能验证
            self.assertTrue(api_keys.verify(p, a["plaintext"])[0])
            self.assertTrue(api_keys.verify(p, b["plaintext"],
                                            required_scope="proxy")[0])
            # 停用 a
            api_keys.set_enabled(p, a["id"], False)
            self.assertFalse(api_keys.verify(p, a["plaintext"])[0])
            # 重命名 b
            api_keys.update_label(p, b["id"], "推理客户端-2")
            self.assertEqual(api_keys.list_keys(p)[1]["label"], "推理客户端-2")
            # 回收 a
            api_keys.revoke_key(p, a["id"])
            self.assertEqual(len(api_keys.list_keys(p)), 1)
            self.assertEqual(api_keys.list_keys(p)[0]["id"], b["id"])


if __name__ == "__main__":
    unittest.main()
