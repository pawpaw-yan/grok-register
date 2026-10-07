# -*- coding: utf-8 -*-
"""core.display_runtime：后端解析矩阵、Xvfb 幂等拉起、VNC 命令形状、headless 联动。"""

import os
import sys
import unittest
from unittest.mock import patch

from core import display_runtime as dr

LINUX = patch.object(dr, "is_linux", return_value=True)


class DisplayRuntimeTestBase(unittest.TestCase):
    def setUp(self):
        self._saved_config = dr.config
        dr.config = {
            "display_backend": "auto",
            "display_num": 99,
            "display_screen": "1280x900x24",
            "display_wait_sec": 1,
            "display_vnc_enabled": False,
            "display_vnc_port": 5999,
            "display_vnc_viewonly": True,
            "display_vnc_password": "",
            "hidden_window": False,
        }
        self._saved_env = os.environ.get("DISPLAY")

    def tearDown(self):
        dr.config = self._saved_config
        if self._saved_env is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = self._saved_env


class BackendResolutionTests(DisplayRuntimeTestBase):
    def test_auto_uses_existing_display_without_spawning(self):
        os.environ["DISPLAY"] = ":77"
        with LINUX, patch.object(dr, "ensure_xvfb") as xvfb:
            display = dr.prepare_display()
        self.assertEqual(display, ":77")
        xvfb.assert_not_called()

    def test_auto_without_display_spawns_xvfb_and_exports_env(self):
        os.environ.pop("DISPLAY", None)
        with LINUX, patch.object(dr, "_unix_socket_alive", return_value=True), \
             patch.object(dr, "_ensure_vnc") as vnc:
            display = dr.prepare_display()
        self.assertEqual(display, ":99")
        self.assertEqual(os.environ["DISPLAY"], ":99")
        vnc.assert_called_once_with(":99", None)

    def test_native_without_display_raises_clear_error(self):
        dr.config["display_backend"] = "native"
        os.environ.pop("DISPLAY", None)
        with LINUX, patch.object(dr, "_unix_socket_alive", return_value=False):
            with self.assertRaises(dr.DisplayBackendError):
                dr.prepare_display()

    def test_native_with_display_passthrough(self):
        dr.config["display_backend"] = "native"
        os.environ["DISPLAY"] = ":5"
        with LINUX, patch.object(dr, "_ensure_vnc") as vnc:
            display = dr.prepare_display()
        self.assertEqual(display, ":5")
        vnc.assert_called_once_with(":5", None)

    def test_headless_returns_empty_and_flags_headless(self):
        dr.config["display_backend"] = "headless"
        os.environ["DISPLAY"] = ":1"
        with LINUX:
            display = dr.prepare_display()
        self.assertEqual(display, "")
        self.assertTrue(dr.headless_requested())

    def test_invalid_backend_falls_back_to_auto(self):
        dr.config["display_backend"] = "weird"
        self.assertEqual(dr.resolve_backend(), "auto")

    def test_non_linux_passthrough_without_spawning(self):
        os.environ["DISPLAY"] = ":3"
        with patch.object(dr.sys, "platform", "win32"), patch.object(dr, "ensure_xvfb") as xvfb:
            display = dr.prepare_display()
        self.assertEqual(display, ":3")
        xvfb.assert_not_called()


class XvfbTests(DisplayRuntimeTestBase):
    def test_alive_socket_short_circuits(self):
        with patch.object(dr, "_unix_socket_alive", return_value=True), \
             patch.object(dr, "shutil") as fake_shutil, \
             patch.object(dr, "subprocess") as fake_sub:
            display = dr.ensure_xvfb(99, "1280x900x24")
        self.assertEqual(display, ":99")
        fake_shutil.which.assert_not_called()
        fake_sub.Popen.assert_not_called()

    def test_missing_binary_raises_with_hint(self):
        with patch.object(dr, "_unix_socket_alive", return_value=False), \
             patch.object(dr.shutil, "which", return_value=None):
            with self.assertRaisesRegex(dr.DisplayBackendError, "apt install xvfb"):
                dr.ensure_xvfb(99, "1280x900x24", wait_sec=1)

    def test_spawn_command_shape_and_wait(self):
        alive = {"v": False}

        def fake_alive(_path):
            alive["v"] = True
            return alive["v"]

        def fake_popen(command, **_kwargs):
            self.assertEqual(command[1], ":99")
            self.assertIn("1280x900x24", command)
            self.assertIn("-nolisten", command)
            self.assertIn("-nolisten", command)
            alive["v"] = True

        def fake_lock_context():
            return _FakeLock()

        class _FakeLock:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with patch.object(dr, "_unix_socket_alive", side_effect=fake_alive), \
             patch.object(dr.shutil, "which", return_value="/usr/bin/Xvfb"), \
             patch.object(dr, "FileLock", fake_lock_context), \
             patch.object(dr.subprocess, "Popen", side_effect=fake_popen):
            display = dr.ensure_xvfb(99, "1280x900x24", wait_sec=2)
        self.assertEqual(display, ":99")

    def test_spawn_timeout_raises(self):
        class _FakeLock:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with patch.object(dr, "_unix_socket_alive", return_value=False), \
             patch.object(dr.shutil, "which", return_value="/usr/bin/Xvfb"), \
             patch.object(dr, "FileLock", lambda *_a, **_k: _FakeLock()), \
             patch.object(dr.subprocess, "Popen"):
            with self.assertRaisesRegex(dr.DisplayBackendError, "仍未监听"):
                dr.ensure_xvfb(99, "1280x900x24", wait_sec=1)


class VncTests(DisplayRuntimeTestBase):
    def test_disabled_is_noop(self):
        dr.config["display_vnc_enabled"] = False
        with patch.object(dr, "shutil") as fake_shutil:
            dr._ensure_vnc(":99")
        fake_shutil.which.assert_not_called()

    def test_missing_binary_warns_without_raising(self):
        dr.config["display_vnc_enabled"] = True
        logs = []
        with patch.object(dr, "_tcp_alive", return_value=False), \
             patch.object(dr.shutil, "which", return_value=None):
            dr._ensure_vnc(":99", log_callback=logs.append)
        self.assertTrue(any("x11vnc 未安装" in line for line in logs))

    def test_alive_port_is_reused(self):
        dr.config["display_vnc_enabled"] = True
        logs = []
        with patch.object(dr, "_tcp_alive", return_value=True), \
             patch.object(dr, "shutil") as fake_shutil, \
             patch.object(dr.subprocess, "Popen") as popen:
            dr._ensure_vnc(":99", log_callback=logs.append)
        fake_shutil.which.assert_not_called()
        popen.assert_not_called()
        self.assertTrue(any("复用" in line for line in logs))

    def test_command_shape_viewonly_and_nopw(self):
        dr.config["display_vnc_enabled"] = True
        captured = {}

        def fake_popen(command, **_kwargs):
            captured["cmd"] = list(command)

        with patch.object(dr, "_tcp_alive", side_effect=[False, True]), \
             patch.object(dr.shutil, "which", return_value="/usr/bin/x11vnc"), \
             patch.object(dr.subprocess, "Popen", side_effect=fake_popen):
            dr._ensure_vnc(":99")
        cmd = captured["cmd"]
        self.assertEqual(cmd[cmd.index("-display") + 1], ":99")
        self.assertEqual(cmd[cmd.index("-rfbport") + 1], "5999")
        self.assertEqual(cmd[cmd.index("-listen") + 1], "127.0.0.1")
        self.assertIn("-viewonly", cmd)
        self.assertIn("-nopw", cmd)
        self.assertNotIn("-passwd", cmd)

    def test_command_shape_password_and_interactive(self):
        dr.config["display_vnc_enabled"] = True
        dr.config["display_vnc_viewonly"] = False
        dr.config["display_vnc_password"] = "secret"
        captured = {}

        def fake_popen(command, **_kwargs):
            captured["cmd"] = list(command)

        with patch.object(dr, "_tcp_alive", side_effect=[False, True]), \
             patch.object(dr.shutil, "which", return_value="/usr/bin/x11vnc"), \
             patch.object(dr.subprocess, "Popen", side_effect=fake_popen):
            dr._ensure_vnc(":99")
        cmd = captured["cmd"]
        self.assertEqual(cmd[cmd.index("-passwd") + 1], "secret")
        self.assertNotIn("-viewonly", cmd)
        self.assertNotIn("-nopw", cmd)

    def test_non_loopback_without_password_refuses(self):
        dr.config["display_vnc_enabled"] = True
        dr.config["display_vnc_listen"] = "0.0.0.0"
        dr.config["display_vnc_password"] = ""
        logs = []
        with patch.object(dr, "_tcp_alive", return_value=False),              patch.object(dr.shutil, "which", return_value="/usr/bin/x11vnc"),              patch.object(dr.subprocess, "Popen") as popen:
            dr._ensure_vnc(":99", log_callback=logs.append)
        popen.assert_not_called()
        self.assertTrue(any("拒绝无密码暴露公网" in line for line in logs))

    def test_non_loopback_with_password_starts_direct(self):
        dr.config["display_vnc_enabled"] = True
        dr.config["display_vnc_listen"] = "0.0.0.0"
        dr.config["display_vnc_password"] = "secret"
        captured = {}
        logs = []

        def fake_popen(command, **_kwargs):
            captured["cmd"] = list(command)

        with patch.object(dr, "_tcp_alive", side_effect=[False, True]),              patch.object(dr.shutil, "which", return_value="/usr/bin/x11vnc"),              patch.object(dr.subprocess, "Popen", side_effect=fake_popen):
            dr._ensure_vnc(":99", log_callback=logs.append)
        cmd = captured["cmd"]
        self.assertEqual(cmd[cmd.index("-listen") + 1], "0.0.0.0")
        self.assertIn("-passwd", cmd)
        self.assertTrue(any("直连" in line for line in logs))

    def test_hidden_window_conflict_warning(self):
        dr.config["display_vnc_enabled"] = True
        dr.config["hidden_window"] = True
        logs = []
        with patch.object(dr, "_tcp_alive", side_effect=[False, True]), \
             patch.object(dr.shutil, "which", return_value="/usr/bin/x11vnc"), \
             patch.object(dr.subprocess, "Popen"):
            dr._ensure_vnc(":99", log_callback=logs.append)
        self.assertTrue(any("hidden_window" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
