#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_command / quote_arg 单元测试。

运行：python -m unittest tests.test_launcher
"""

import os
import sys
import unittest

# 允许从项目根目录导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.launcher import build_command, quote_arg


class TestBuildCommand(unittest.TestCase):
    def setUp(self):
        self.exe = "/path/to/llama-server"
        self.model = "/models/test.gguf"
        self.base_profile = {
            "context_size": 2048, "parallel": 2, "batch_size": 512,
            "ubatch_size": 512, "gpu_layers": -1, "threads": 8,
            "flash_attn": True, "cont_batching": True,
            "mlock": False, "no_mmap": False, "embedding": False,
            "reranking": False, "jinja": True, "verbose": False,
            "pooling": "", "chat_template": "", "draft_model": "",
            "grammar_file": "", "extra_args": "",
        }

    def test_basic_args_present(self):
        cmd = build_command(self.exe, self.model, self.base_profile,
                            host="127.0.0.1", port=8080)
        self.assertEqual(cmd[0], self.exe)
        self.assertIn("-m", cmd)
        idx = cmd.index("-m")
        self.assertEqual(cmd[idx + 1], self.model)
        self.assertIn("--host", cmd)
        self.assertIn("--port", cmd)

    def test_host_port_omitted_when_empty(self):
        cmd = build_command(self.exe, self.model, self.base_profile)
        self.assertNotIn("--host", cmd)
        self.assertNotIn("--port", cmd)

    def test_int_fields_mapped_to_flags(self):
        cmd = build_command(self.exe, self.model, self.base_profile,
                            host="0.0.0.0", port=1234)
        # context_size -> -c
        self.assertEqual(cmd[cmd.index("-c") + 1], "2048")
        # parallel -> -np
        self.assertEqual(cmd[cmd.index("-np") + 1], "2")
        # batch_size -> -b
        self.assertEqual(cmd[cmd.index("-b") + 1], "512")
        # ubatch_size -> -ub
        self.assertEqual(cmd[cmd.index("-ub") + 1], "512")
        # gpu_layers=-1 保留
        self.assertEqual(cmd[cmd.index("-ngl") + 1], "-1")
        # threads -> -t
        self.assertEqual(cmd[cmd.index("-t") + 1], "8")

    def test_zero_or_empty_int_omitted(self):
        prof = dict(self.base_profile)
        prof["context_size"] = 0
        prof["threads"] = ""
        prof["gpu_layers"] = 0
        cmd = build_command(self.exe, self.model, prof, host="127.0.0.1", port=8080)
        self.assertNotIn("-c", cmd)
        self.assertNotIn("-t", cmd)
        self.assertNotIn("-ngl", cmd)

    def test_flash_attn_explicit_value(self):
        # -fa 必须显式传值 on（记忆中的陷阱）
        cmd = build_command(self.exe, self.model, self.base_profile,
                            host="127.0.0.1", port=8080)
        self.assertIn("-fa", cmd)
        self.assertEqual(cmd[cmd.index("-fa") + 1], "on")

    def test_flash_attn_off_when_false(self):
        prof = dict(self.base_profile)
        prof["flash_attn"] = False
        cmd = build_command(self.exe, self.model, prof, host="127.0.0.1", port=8080)
        self.assertNotIn("-fa", cmd)

    def test_bool_flags(self):
        prof = dict(self.base_profile)
        prof["mlock"] = True
        prof["no_mmap"] = True
        prof["embedding"] = True
        prof["reranking"] = True
        prof["jinja"] = True
        prof["verbose"] = True
        cmd = build_command(self.exe, self.model, prof, host="127.0.0.1", port=8080)
        for flag in ("--mlock", "--no-mmap", "--embedding", "--reranking", "--jinja", "-v"):
            self.assertIn(flag, cmd)

    def test_string_fields(self):
        prof = dict(self.base_profile)
        prof["pooling"] = "mean"
        prof["chat_template"] = "chatml"
        prof["grammar_file"] = "/g/grammar.gbnf"
        cmd = build_command(self.exe, self.model, prof, host="127.0.0.1", port=8080)
        self.assertEqual(cmd[cmd.index("--pooling") + 1], "mean")
        self.assertEqual(cmd[cmd.index("--chat-template") + 1], "chatml")
        self.assertEqual(cmd[cmd.index("--grammar-file") + 1], "/g/grammar.gbnf")

    def test_draft_model_relative(self):
        prof = dict(self.base_profile)
        prof["draft_model"] = "draft.gguf"
        cmd = build_command(self.exe, "/models/main.gguf", prof,
                            host="127.0.0.1", port=8080)
        idx = cmd.index("-md")
        # 相对路径基于模型所在目录
        self.assertTrue(cmd[idx + 1].endswith("draft.gguf"))
        self.assertIn("/models", cmd[idx + 1].replace("\\", "/"))

    def test_draft_model_absolute(self):
        # 用当前平台真正的绝对路径（Windows 需盘符），否则 Path.is_absolute() 返回 False
        abs_draft = os.path.abspath(os.path.join(os.sep, "abs", "draft.gguf"))
        prof = dict(self.base_profile)
        prof["draft_model"] = abs_draft
        cmd = build_command(self.exe, self.model, prof, host="127.0.0.1", port=8080)
        self.assertEqual(cmd[cmd.index("-md") + 1], abs_draft)

    def test_extra_args_split(self):
        prof = dict(self.base_profile)
        prof["extra_args"] = "--top-k 40 --top-p 0.9"
        cmd = build_command(self.exe, self.model, prof, host="127.0.0.1", port=8080)
        self.assertIn("--top-k", cmd)
        self.assertIn("40", cmd)
        self.assertIn("--top-p", cmd)
        self.assertIn("0.9", cmd)


class TestQuoteArg(unittest.TestCase):
    def test_no_quote_plain(self):
        self.assertEqual(quote_arg("plain"), "plain")

    def test_quote_space(self):
        self.assertEqual(quote_arg("a b"), '"a b"')

    def test_quote_tab(self):
        self.assertEqual(quote_arg("a\tb"), '"a\tb"')

    def test_quote_escapes_inner(self):
        self.assertEqual(quote_arg('a "b"'), '"a \\"b\\""')


if __name__ == "__main__":
    unittest.main()
