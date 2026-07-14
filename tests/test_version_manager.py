#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""version_manager 单元测试：资产名解析、备份检测、回滚。

运行：python -m unittest tests.test_version_manager
"""

import os
import io
import json
import sys
import tempfile
import urllib.error
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import version_manager as vm


class FakeGithubResponse:
    def __init__(self, payload, headers=None):
        self._payload = payload
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


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


class TestFetchReleases(unittest.TestCase):
    def setUp(self):
        vm._releases_cache.update({
            "per_page": None,
            "fetched_at": 0.0,
            "etag": "",
            "last_modified": "",
            "data": None,
        })

    def test_fetch_releases_uses_memory_cache(self):
        payload = [{
            "tag_name": "b9999",
            "name": "release",
            "published_at": "2026-01-02T00:00:00Z",
            "assets": [{
                "name": "llama-b9999-bin-win-cpu-x64.zip",
                "browser_download_url": "https://example.test/a.zip",
                "size": 123,
            }],
        }]
        with mock.patch("core.version_manager.urllib.request.urlopen",
                        return_value=FakeGithubResponse(payload, {"ETag": '"abc"'})) as urlopen:
            first = vm.fetch_releases()
            second = vm.fetch_releases()
        self.assertEqual(first, second)
        self.assertEqual(urlopen.call_count, 1)

    def test_github_403_non_rate_limit_keeps_detail(self):
        error = urllib.error.HTTPError(
            "https://api.github.com/test", 403, "Forbidden",
            {"X-RateLimit-Remaining": "42"},
            io.BytesIO(b'{"message":"Resource protected by organization policy"}'),
        )
        msg = vm._github_error_message(error)
        error.close()
        self.assertIn("HTTP 403", msg)
        self.assertIn("organization policy", msg)
        self.assertNotIn("频率限制", msg)

    def test_github_403_rate_limit_by_remaining_header(self):
        error = urllib.error.HTTPError(
            "https://api.github.com/test", 403, "Forbidden",
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "123456"},
            io.BytesIO(b'{"message":"API rate limit exceeded"}'),
        )
        msg = vm._github_error_message(error)
        error.close()
        self.assertIn("频率限制", msg)
        self.assertIn("123456", msg)


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
