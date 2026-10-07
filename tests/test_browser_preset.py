# -*- coding: utf-8 -*-
"""browser_preset：system/cloak 浏览器来源解析与 CloakBrowser 自动定位。"""

import os
import tempfile
import unittest
from unittest.mock import patch

from core import browser_runtime as br


class BrowserPresetTests(unittest.TestCase):
    def setUp(self):
        self._saved_config = br._config
        br._config = {"browser_path": "", "browser_preset": "system"}

    def tearDown(self):
        br._config = self._saved_config

    def _fake_home(self, versions):
        tmp = tempfile.mkdtemp(prefix="cloak_preset_test_")
        for version in versions:
            exe_dir = os.path.join(tmp, ".cloakbrowser", "chromium-%s" % version)
            os.makedirs(exe_dir, exist_ok=True)
            exe = os.path.join(exe_dir, "chrome.exe")
            with open(exe, "w", encoding="utf-8") as handle:
                handle.write("stub")
        return tmp

    def test_system_preset_returns_empty(self):
        self.assertEqual(br.resolve_browser_path(), "")

    def test_explicit_browser_path_overrides_preset(self):
        br._config["browser_preset"] = "cloak"
        br._config["browser_path"] = r"D:\custom\chrome.exe"
        self.assertEqual(br.resolve_browser_path(), r"D:\custom\chrome.exe")

    def test_cloak_picks_highest_version(self):
        br._config["browser_preset"] = "cloak"
        home = self._fake_home(["145.0.1.2", "146.0.7680.177.5"])
        with patch.object(br.os.path, "expanduser", return_value=home):
            resolved = br.resolve_browser_path()
        expected = os.path.join(home, ".cloakbrowser", "chromium-146.0.7680.177.5", "chrome.exe")
        self.assertEqual(resolved, expected)

    def test_cloak_missing_install_returns_empty(self):
        home = self._fake_home([])
        br._config["browser_preset"] = "cloak"
        with patch.object(br.os.path, "expanduser", return_value=home):
            self.assertEqual(br.resolve_browser_path(), "")

    def test_cloak_version_key_malformed_dir_does_not_crash(self):
        br._config["browser_preset"] = "cloak"
        home = self._fake_home(["145.0.1.2"])
        bad_dir = os.path.join(home, ".cloakbrowser", "chromium-not-a-version")
        os.makedirs(bad_dir, exist_ok=True)
        with open(os.path.join(bad_dir, "chrome.exe"), "w", encoding="utf-8") as handle:
            handle.write("stub")
        with patch.object(br.os.path, "expanduser", return_value=home):
            resolved = br.resolve_browser_path()
        self.assertTrue(resolved.endswith("chromium-145.0.1.2" + os.sep + "chrome.exe"))

    def test_unknown_preset_behaves_like_system(self):
        br._config["browser_preset"] = "whatever"
        self.assertEqual(br.resolve_browser_path(), "")


if __name__ == "__main__":
    unittest.main()
