#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paths 模块单元测试：路径检测、模型枚举、目录浏览、健康检查辅助。

运行：python -m unittest tests.test_paths
"""

import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.paths import (browse_directory, check_port_bindable, detect_llamacpp,
                        list_model_files, model_exists, resolve_model_path,
                        server_executable_candidates)


class TestServerExecutableCandidates(unittest.TestCase):
    def test_empty_dir_returns_empty(self):
        self.assertEqual(server_executable_candidates(""), [])

    def test_finds_exe_in_root(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / ("llama-server.exe" if os.name == "nt" else "llama-server")
            exe.write_text("x")
            cands = server_executable_candidates(d)
            self.assertEqual(len(cands), 1)

    def test_finds_exe_in_build_bin(self):
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "build" / "bin"
            sub.mkdir(parents=True)
            exe = sub / ("llama-server.exe" if os.name == "nt" else "llama-server")
            exe.write_text("x")
            cands = server_executable_candidates(d)
            self.assertEqual(len(cands), 1)
            self.assertIn("build", cands[0])


class TestDetectLlamacpp(unittest.TestCase):
    def test_empty(self):
        status, msg, exe = detect_llamacpp("")
        self.assertEqual(status, "bad")
        self.assertEqual(exe, "")

    def test_nonexistent(self):
        status, msg, exe = detect_llamacpp("/no/such/dir/xyz")
        self.assertEqual(status, "bad")

    def test_ok(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / ("llama-server.exe" if os.name == "nt" else "llama-server")
            exe.write_text("x")
            status, msg, exe_path = detect_llamacpp(d)
            self.assertEqual(status, "ok")
            self.assertTrue(exe_path)


class TestListModelFiles(unittest.TestCase):
    def test_empty_dir(self):
        self.assertEqual(list_model_files(""), [])

    def test_nonexistent_dir(self):
        self.assertEqual(list_model_files("/no/such/dir/xyz"), [])

    def test_lists_gguf_top_level(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.gguf").write_text("x")
            (Path(d) / "b.gguf").write_text("x")
            (Path(d) / "ignore.txt").write_text("x")
            result = list_model_files(d)
            self.assertIn("a.gguf", result)
            self.assertIn("b.gguf", result)
            self.assertEqual(len(result), 2)

    def test_lists_subdir_gguf(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "sub").mkdir()
            (Path(d) / "sub" / "c.gguf").write_text("x")
            result = list_model_files(d)
            self.assertIn("sub/c.gguf", result)

    def test_case_insensitive_ext(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "M.GGUF").write_text("x")
            result = list_model_files(d)
            self.assertIn("M.GGUF", result)


class TestBrowseDirectory(unittest.TestCase):
    def test_empty_path_returns_roots(self):
        r = browse_directory("")
        # 实现始终返回 error 字段（空串表示无错）
        self.assertEqual(r.get("error", "(missing)"), "")
        self.assertIn("entries", r)
        self.assertGreater(len(r["entries"]), 0)

    def test_nonexistent(self):
        r = browse_directory("/no/such/dir/xyz")
        self.assertEqual(r["error"], "不是有效目录")
        self.assertEqual(r["entries"], [])

    def test_lists_dirs_first(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "zdir").mkdir()
            (Path(d) / "a.gguf").write_text("x")
            r = browse_directory(d)
            types = [e["type"] for e in r["entries"]]
            # dir 在前
            self.assertEqual(types, sorted(types, key=lambda t: t != "dir"))

    def test_parent_computed(self):
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "sub"
            sub.mkdir()
            r = browse_directory(str(sub))
            self.assertTrue(r["parent"])

    def test_filters_non_browsable_files(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "model.gguf").write_text("x")
            (Path(d) / "config.json").write_text("x")
            (Path(d) / "random.bin").write_text("x")  # 应被隐藏
            r = browse_directory(d)
            names = [e["name"] for e in r["entries"] if e["type"] == "file"]
            self.assertIn("model.gguf", names)
            self.assertIn("config.json", names)
            self.assertNotIn("random.bin", names)


class TestHealthcheckHelpers(unittest.TestCase):
    def test_check_port_bindable_ok(self):
        # 通过 socket 绑定端口 0 获取系统分配的空闲端口，再探测该端口
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        ok, err = check_port_bindable("127.0.0.1", free_port)
        self.assertTrue(ok)

    def test_check_port_invalid_int(self):
        ok, err = check_port_bindable("127.0.0.1", "not-a-number")
        self.assertFalse(ok)
        self.assertIn("非整数", err)

    def test_check_port_out_of_range(self):
        ok, err = check_port_bindable("127.0.0.1", 99999)
        self.assertFalse(ok)
        self.assertIn("范围", err)

    def test_resolve_model_absolute(self):
        # 用当前平台真正的绝对路径（Windows 需盘符）
        abs_path = os.path.abspath(os.path.join(os.sep, "abs", "m.gguf"))
        self.assertEqual(resolve_model_path(abs_path, "/models"), abs_path)

    def test_resolve_model_relative(self):
        self.assertEqual(resolve_model_path("m.gguf", "/models"),
                         str(Path("/models") / "m.gguf"))

    def test_resolve_model_empty(self):
        self.assertEqual(resolve_model_path("", "/models"), "")

    def test_model_exists_gguf(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.gguf"
            p.write_text("x")
            exists, is_gguf = model_exists(str(p))
            self.assertTrue(exists)
            self.assertTrue(is_gguf)

    def test_model_exists_wrong_ext(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.bin"
            p.write_text("x")
            exists, is_gguf = model_exists(str(p))
            self.assertTrue(exists)
            self.assertFalse(is_gguf)

    def test_model_exists_missing(self):
        exists, is_gguf = model_exists("/no/such/file.gguf")
        self.assertFalse(exists)


if __name__ == "__main__":
    unittest.main()
