#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""version_manager 单元测试：资产名解析、备份检测、回滚。

运行：python -m unittest tests.test_version_manager
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import version_manager as vm


class TestParseAssetName(unittest.TestCase):
    def test_win_cpu_x64_zip(self):
        info = vm.parse_asset_name("llama-b9828-bin-win-cpu-x64.zip")
        self.assertIsNotNone(info)
        self.assertEqual(info["build"], "b9828")
        self.assertEqual(info["os"], "win")
        self.assertEqual(info["arch"], "x64")
        self.assertEqual(info["variant"], "cpu")
        self.assertEqual(info["ext"], ".zip")

    def test_ubuntu_x64_targz(self):
        info = vm.parse_asset_name("llama-b9828-bin-ubuntu-x64.tar.gz")
        self.assertIsNotNone(info)
        self.assertEqual(info["os"], "ubuntu")
        self.assertEqual(info["arch"], "x64")
        self.assertEqual(info["variant"], "default")
        self.assertEqual(info["ext"], ".tar.gz")

    def test_macos_arm64_targz(self):
        info = vm.parse_asset_name("llama-b9828-bin-macos-arm64.tar.gz")
        self.assertIsNotNone(info)
        self.assertEqual(info["os"], "macos")
        self.assertEqual(info["arch"], "arm64")

    def test_tgz_extension(self):
        info = vm.parse_asset_name("llama-b9828-bin-win-x64.tgz")
        self.assertIsNotNone(info)
        self.assertEqual(info["ext"], ".tar.gz")

    def test_rejects_non_llama(self):
        self.assertIsNone(vm.parse_asset_name("random-file.zip"))

    def test_rejects_no_bin(self):
        self.assertIsNone(vm.parse_asset_name("llama-b9828-release-x64.zip"))

    def test_rejects_unsupported_ext(self):
        self.assertIsNone(vm.parse_asset_name("llama-b9828-bin-win-x64.7z"))

    def test_variant_multi_segment(self):
        info = vm.parse_asset_name("llama-b9828-bin-win-cuda-cu12-x64.zip")
        self.assertIsNotNone(info)
        self.assertEqual(info["variant"], "cuda-cu12")

    def test_unknown_arch_kept_as_variant(self):
        # 无标准 arch 段时，末尾归入 variant
        info = vm.parse_asset_name("llama-b9828-bin-win-cpu.zip")
        self.assertIsNotNone(info)
        self.assertEqual(info["os"], "win")
        self.assertEqual(info["variant"], "cpu")


class TestAssetOsGroup(unittest.TestCase):
    def test_windows_group(self):
        info = vm.parse_asset_name("llama-b9828-bin-win-cpu-x64.zip")
        self.assertEqual(vm.asset_os_group(info), "windows")

    def test_linux_group(self):
        info = vm.parse_asset_name("llama-b9828-bin-ubuntu-x64.tar.gz")
        self.assertEqual(vm.asset_os_group(info), "linux")

    def test_macos_group(self):
        info = vm.parse_asset_name("llama-b9828-bin-macos-arm64.tar.gz")
        self.assertEqual(vm.asset_os_group(info), "macos")

    def test_others_group(self):
        info = vm.parse_asset_name("llama-b9828-bin-freesbie-x64.zip")
        self.assertEqual(vm.asset_os_group(info), "others")

    def test_empty_info(self):
        self.assertEqual(vm.asset_os_group(None), "")


class TestVariantLabel(unittest.TestCase):
    def test_default(self):
        self.assertEqual(vm.variant_label({"variant": ""}), "default")
        self.assertEqual(vm.variant_label({}), "default")

    def test_named(self):
        self.assertEqual(vm.variant_label({"variant": "cpu"}), "cpu")


class TestPlatformTokens(unittest.TestCase):
    def test_arch_token_normalize(self):
        # 不依赖实际机器，只测逻辑分支
        self.assertIn(vm.current_arch_token(), ("x64", "arm64") + (vm.platform.machine().lower(),))

    def test_os_token(self):
        self.assertIn(vm.current_os_token(), ("win", "ubuntu", "macos"))


class TestHasBackup(unittest.TestCase):
    def test_no_backup_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(vm.has_backup(d))

    def test_empty_backup_dir(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "_backup").mkdir()
            self.assertFalse(vm.has_backup(d))

    def test_has_files(self):
        with tempfile.TemporaryDirectory() as d:
            bd = Path(d) / "_backup"
            bd.mkdir()
            (bd / "llama-server.exe").write_text("x")
            self.assertTrue(vm.has_backup(d))


class TestRollbackAsset(unittest.TestCase):
    def test_no_backup_fails(self):
        with tempfile.TemporaryDirectory() as d:
            ok, msg = vm.rollback_asset(d)
            self.assertFalse(ok)
            self.assertIn("未找到", msg)

    def test_empty_backup_fails(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "_backup").mkdir()
            ok, msg = vm.rollback_asset(d)
            self.assertFalse(ok)
            self.assertIn("为空", msg)

    def test_rollback_restores_files(self):
        with tempfile.TemporaryDirectory() as d:
            # 当前目录有一个新版 llama-server
            cur = Path(d) / "llama-server.exe"
            cur.write_text("NEW")
            # 备份一个旧版
            bd = Path(d) / "_backup"
            bd.mkdir()
            (bd / "llama-server.exe").write_text("OLD")
            progress = []
            ok, msg = vm.rollback_asset(d, progress_cb=lambda p, c, t: progress.append((p, c, t)))
            self.assertTrue(ok)
            self.assertEqual(cur.read_text(), "OLD")
            self.assertTrue(progress)  # 进度回调被调用

    def test_rollback_progress_callback(self):
        with tempfile.TemporaryDirectory() as d:
            bd = Path(d) / "_backup"
            bd.mkdir()
            (bd / "a.dll").write_text("x")
            (bd / "b.dll").write_text("y")
            calls = []
            ok, msg = vm.rollback_asset(d, progress_cb=lambda p, c, t: calls.append(c))
            self.assertTrue(ok)
            self.assertIn(0, calls)
            self.assertIn(2, calls)


if __name__ == "__main__":
    unittest.main()
