#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ProfileManager 与方案校验单元测试。

运行：python -m unittest tests.test_profiles
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.profiles import (ProfileManager, list_config_files, load_json,
                           normalize_profile, save_json, validate_profile)


SAMPLE = {
    "cpu": {"context_size": 0, "threads": 12, "gpu_layers": 0, "flash_attn": True},
    "gpu": {"context_size": 0, "threads": 0, "gpu_layers": -1, "flash_attn": True},
}


class TestLoadSaveJson(unittest.TestCase):
    def test_load_json_missing_file(self):
        self.assertIsNone(load_json("/no/such/file.json"))
        self.assertEqual(load_json("/no/such/file.json", {"a": 1}), {"a": 1})

    def test_load_json_invalid(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("not json {{{")
            path = f.name
        try:
            self.assertEqual(load_json(path, {"def": 1}), {"def": 1})
        finally:
            os.unlink(path)

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "c.json")
            save_json(p, {"中文": "值", "n": 1})
            data = load_json(p)
            self.assertEqual(data["中文"], "值")
            self.assertEqual(data["n"], 1)

    def test_save_json_utf8_no_ascii_escape(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "c.json")
            save_json(p, {"k": "中文"})
            with open(p, "r", encoding="utf-8") as f:
                raw = f.read()
            self.assertIn("中文", raw)  # 未被 \uXXXX 转义


class TestProfileManager(unittest.TestCase):
    def test_load_valid(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "p.json")
            save_json(p, SAMPLE)
            pm = ProfileManager(p)
            self.assertEqual(set(pm.names()), {"cpu", "gpu"})
            self.assertEqual(pm.get("cpu"), SAMPLE["cpu"])
            self.assertIsNone(pm.get("nope"))

    def test_load_empty_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "empty.json")
            Path(p).write_text("{}")
            pm = ProfileManager(p)
            self.assertEqual(pm.names(), [])

    def test_load_invalid_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "bad.json")
            Path(p).write_text("not json")
            pm = ProfileManager(p)
            self.assertEqual(pm.names(), [])

    def test_save_persists(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "p.json")
            save_json(p, SAMPLE)
            pm = ProfileManager(p)
            pm.upsert("mix", {"gpu_layers": 10})
            self.assertTrue(pm.save())
            # 重新加载验证
            pm2 = ProfileManager(p)
            self.assertIn("mix", pm2.names())
            self.assertEqual(pm2.get("mix")["gpu_layers"], 10)

    def test_upsert_and_delete(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "p.json")
            save_json(p, SAMPLE)
            pm = ProfileManager(p)
            self.assertTrue(pm.upsert("new", {"threads": 4}))
            self.assertIn("new", pm.names())
            self.assertFalse(pm.upsert("", {}))  # 空名拒绝
            self.assertTrue(pm.delete("new"))
            self.assertNotIn("new", pm.names())
            self.assertFalse(pm.delete("new"))  # 再次删除返回 False

    def test_rename(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "p.json")
            save_json(p, SAMPLE)
            pm = ProfileManager(p)
            self.assertTrue(pm.rename("cpu", "cpu2"))
            self.assertIn("cpu2", pm.names())
            self.assertNotIn("cpu", pm.names())
            self.assertFalse(pm.rename("cpu2", "gpu"))  # 目标已存在
            self.assertFalse(pm.rename("nope", "x"))


class TestNormalizeProfile(unittest.TestCase):
    def test_fills_defaults(self):
        prof, warns = normalize_profile({})
        self.assertEqual(prof["context_size"], 0)
        self.assertEqual(prof["flash_attn"], False)
        self.assertEqual(prof["pooling"], "")

    def test_invalid_int_reset(self):
        prof, warns = normalize_profile({"context_size": "abc", "threads": "xyz"})
        self.assertEqual(prof["context_size"], 0)
        self.assertEqual(prof["threads"], 0)
        self.assertTrue(any("context_size" in w for w in warns))

    def test_keeps_valid_values(self):
        prof, warns = normalize_profile({"gpu_layers": -1, "flash_attn": True})
        self.assertEqual(prof["gpu_layers"], -1)
        self.assertEqual(prof["flash_attn"], True)
        self.assertEqual(warns, [])

    def test_non_dict_returns_empty(self):
        prof, warns = normalize_profile("not a dict")
        self.assertEqual(prof, {})
        self.assertEqual(len(warns), 1)


class TestValidateProfile(unittest.TestCase):
    def test_embedding_reranking_mutex(self):
        errors, warns = validate_profile({"embedding": True, "reranking": True})
        self.assertTrue(any("互斥" in e for e in errors))

    def test_clean_profile_no_errors(self):
        errors, warns = validate_profile({"embedding": False, "reranking": False})
        self.assertEqual(errors, [])


class TestListConfigFiles(unittest.TestCase):
    def test_lists_json_only(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.json").write_text("{}")
            (Path(d) / "b.json").write_text("{}")
            (Path(d) / "c.txt").write_text("x")
            result = list_config_files(d)
            names = [Path(f).name for f in result]
            self.assertIn("a.json", names)
            self.assertIn("b.json", names)
            self.assertNotIn("c.txt", names)

    def test_nonexistent_dir(self):
        self.assertEqual(list_config_files("/no/such/dir"), [])


if __name__ == "__main__":
    unittest.main()
