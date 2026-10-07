"""验证 grok2api Build 入库通道：模式路由、转换回退、本地留档、代理与 http 守卫。"""

import sys
import types
import unittest
from unittest.mock import patch

from registration import account_outputs as ao


def _fake_entry(email="a@example.com"):
    return {
        "provider": "grok_build",
        "name": email,
        "client_id": "b1a00492-073a-47ea-816f-4c329264a828",
        "access_token": "acc",
        "refresh_token": "ref",
        "token_type": "Bearer",
        "email": email,
        "user_id": "uid-1",
        "expires_at": "2026-10-06T14:00:00Z",
    }


_SENTINEL = object()
_REBIND_NAMES = (
    "http_get", "http_post", "log_exception",
    "RemoteTokenCompatibilityError", "RemoteTokenRequestError",
)


class Grok2ApiBuildIngestTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        # configure_token_runtime 会在模块上动态创建全局；先按哨兵捕获原状，tearDown 还原
        self._saved_config = ao.config
        self._saved_attrs = {name: getattr(ao, name, _SENTINEL) for name in _REBIND_NAMES}
        ao.configure_token_runtime(
            {},
            lambda *a, **k: None,
            lambda *a, **k: None,
            lambda msg, exc, cb=None: self.calls.append(("exc", str(msg))),
        )
        ao.config.update({
            "grok2api_remote_base": "https://grok.example.com",
            "grok2api_remote_admin_username": "admin",
            "grok2api_remote_admin_password": "pw",
            "grok2api_ingest_mode": "build",
            "grok2api_build_fallback_web": True,
            "grok2api_build_auth_dir": "./output/build_auth",
            "proxy": "http://127.0.0.1:7890",
        })

    def tearDown(self):
        ao.configure_token_runtime(
            self._saved_config,
            *(self._saved_attrs[name] for name in _REBIND_NAMES[:3]),
            compatibility_error=self._saved_attrs["RemoteTokenCompatibilityError"],
            request_error=self._saved_attrs["RemoteTokenRequestError"],
        )
        for name, value in self._saved_attrs.items():
            if value is _SENTINEL:
                if hasattr(ao, name):
                    delattr(ao, name)

    def test_build_mode_converts_saves_and_pushes(self):
        entry = _fake_entry()
        with patch.object(ao, "_convert_sso_to_build_entry", return_value=entry) as conv, \
             patch.object(ao, "_save_build_entry_local", return_value="p") as save, \
             patch.object(ao, "_push_build_entry_to_grok2api", return_value=True) as push:
            ok = ao.add_token_to_grok2api_remote_pool("sso-raw", email="a@example.com")
        self.assertTrue(ok)
        conv.assert_called_once()
        save.assert_called_once_with(entry)
        push.assert_called_once_with(entry, email="a@example.com", log_callback=None)

    def test_build_mode_falls_back_to_web_on_conversion_failure(self):
        ao.config["grok2api_build_fallback_web"] = True
        with patch.object(ao, "_convert_sso_to_build_entry",
                          side_effect=ao.RemoteTokenRequestError("boom")), \
             patch.object(ao, "_add_token_to_grok2api_go_remote_pool", return_value=True) as go, \
             patch.object(ao, "_push_build_entry_to_grok2api") as push:
            ok = ao.add_token_to_grok2api_remote_pool("sso-raw", email="a@example.com")
        self.assertTrue(ok)
        go.assert_called_once()
        push.assert_not_called()

    def test_build_mode_raises_when_fallback_disabled(self):
        ao.config["grok2api_build_fallback_web"] = False
        with patch.object(ao, "_convert_sso_to_build_entry",
                          side_effect=ao.RemoteTokenRequestError("boom")), \
             patch.object(ao, "_add_token_to_grok2api_go_remote_pool") as go:
            with self.assertRaises(ao.RemoteTokenRequestError):
                ao.add_token_to_grok2api_remote_pool("sso-raw")
        go.assert_not_called()

    def test_both_mode_ingests_web_then_build(self):
        ao.config["grok2api_ingest_mode"] = "both"
        order = []
        entry = _fake_entry()
        with patch.object(ao, "_add_token_to_grok2api_go_remote_pool",
                          side_effect=lambda *a, **k: order.append("web") or True),              patch.object(ao, "_convert_sso_to_build_entry",
                          side_effect=lambda *a, **k: order.append("convert") or entry),              patch.object(ao, "_save_build_entry_local", return_value="p"),              patch.object(ao, "_push_build_entry_to_grok2api",
                          side_effect=lambda *a, **k: order.append("push") or True) as push:
            ok = ao.add_token_to_grok2api_remote_pool("sso-raw", email="a@example.com")
        self.assertTrue(ok)
        self.assertEqual(order, ["web", "convert", "push"])
        push.assert_called_once_with(entry, email="a@example.com", log_callback=None)

    def test_both_mode_web_failure_still_tries_build_then_raises(self):
        ao.config["grok2api_ingest_mode"] = "both"
        entry = _fake_entry()
        with patch.object(ao, "_add_token_to_grok2api_go_remote_pool",
                          side_effect=ao.RemoteTokenRequestError("web down")),              patch.object(ao, "_convert_sso_to_build_entry", return_value=entry),              patch.object(ao, "_save_build_entry_local", return_value="p"),              patch.object(ao, "_push_build_entry_to_grok2api", return_value=True) as push:
            with self.assertRaises(ao.RemoteTokenRequestError):
                ao.add_token_to_grok2api_remote_pool("sso-raw", email="a@example.com")
        push.assert_called_once()

    def test_both_mode_convert_failure_with_fallback_keeps_web_result(self):
        ao.config["grok2api_ingest_mode"] = "both"
        ao.config["grok2api_build_fallback_web"] = True
        with patch.object(ao, "_add_token_to_grok2api_go_remote_pool", return_value=True) as go,              patch.object(ao, "_convert_sso_to_build_entry",
                          side_effect=ao.RemoteTokenRequestError("boom")),              patch.object(ao, "_push_build_entry_to_grok2api") as push:
            ok = ao.add_token_to_grok2api_remote_pool("sso-raw", email="a@example.com")
        self.assertTrue(ok)
        go.assert_called_once()
        push.assert_not_called()

    def test_both_mode_convert_failure_without_fallback_raises(self):
        ao.config["grok2api_ingest_mode"] = "both"
        ao.config["grok2api_build_fallback_web"] = False
        with patch.object(ao, "_add_token_to_grok2api_go_remote_pool", return_value=True) as go,              patch.object(ao, "_convert_sso_to_build_entry",
                          side_effect=ao.RemoteTokenRequestError("boom")),              patch.object(ao, "_push_build_entry_to_grok2api") as push:
            with self.assertRaises(ao.RemoteTokenRequestError):
                ao.add_token_to_grok2api_remote_pool("sso-raw", email="a@example.com")
        go.assert_called_once()
        push.assert_not_called()

    def test_both_mode_without_admin_creds_keeps_build_local_only(self):
        ao.config["grok2api_ingest_mode"] = "both"
        ao.config["grok2api_remote_admin_username"] = ""
        ao.config["grok2api_remote_admin_password"] = ""
        entry = _fake_entry()
        with patch.object(ao, "_add_token_to_grok2api_legacy_remote_pool", return_value=True) as legacy,              patch.object(ao, "_convert_sso_to_build_entry", return_value=entry),              patch.object(ao, "_save_build_entry_local", return_value="p") as save,              patch.object(ao, "_push_build_entry_to_grok2api") as push:
            ok = ao.add_token_to_grok2api_remote_pool("sso-raw", email="a@example.com")
        self.assertTrue(ok)
        legacy.assert_called_once()
        save.assert_called_once_with(entry)
        push.assert_not_called()

    def test_web_mode_skips_conversion(self):
        ao.config["grok2api_ingest_mode"] = "web"
        with patch.object(ao, "_convert_sso_to_build_entry") as conv, \
             patch.object(ao, "_add_token_to_grok2api_go_remote_pool", return_value=True) as go:
            ok = ao.add_token_to_grok2api_remote_pool("sso-raw", email="a@example.com")
        self.assertTrue(ok)
        conv.assert_not_called()
        go.assert_called_once()

    def test_convert_uses_config_proxy_and_auth_code(self):
        seen = {}
        fake_mod = types.SimpleNamespace(
            sso_to_token=lambda sso, proxy="", log=None, prefer="", allow_fallback=True:
                seen.update(sso=sso, proxy=proxy, prefer=prefer) or
                {"access_token": "acc", "refresh_token": "ref", "expires_in": 6},
            token_to_build_entry=lambda token, email="": dict(_fake_entry(email)),
        )
        with patch.dict(sys.modules, {"sso.sso_to_auth_json": fake_mod}):
            entry = ao._convert_sso_to_build_entry("sso-raw", email="a@example.com")
        self.assertEqual(seen, {"sso": "sso-raw", "proxy": "http://127.0.0.1:7890", "prefer": "auth_code"})
        self.assertEqual(entry["provider"], "grok_build")

    def test_convert_proxy_override_takes_priority(self):
        ao.config["grok2api_build_convert_proxy"] = "http://127.0.0.1:9999"
        seen = {}
        fake_mod = types.SimpleNamespace(
            sso_to_token=lambda sso, proxy="", log=None, prefer="", allow_fallback=True:
                seen.update(proxy=proxy) or
                {"access_token": "acc", "refresh_token": "ref", "expires_in": 6},
            token_to_build_entry=lambda token, email="": dict(_fake_entry(email)),
        )
        with patch.dict(sys.modules, {"sso.sso_to_auth_json": fake_mod}):
            ao._convert_sso_to_build_entry("sso-raw")
        self.assertEqual(seen, {"proxy": "http://127.0.0.1:9999"})

    def test_go_api_base_http_guard(self):
        self.assertEqual(
            ao._grok2api_go_api_base("http://127.0.0.1:8000"),
            "http://127.0.0.1:8000/api/admin/v1",
        )
        with self.assertRaises(ao.RemoteTokenRequestError):
            ao._grok2api_go_api_base("http://203.0.113.10:8000")
        self.assertEqual(
            ao._grok2api_go_api_base("http://203.0.113.10:8000", allow_insecure_http=True),
            "http://203.0.113.10:8000/api/admin/v1",
        )
        self.assertEqual(
            ao._grok2api_go_api_base("https://203.0.113.10:8000/api/admin/v1"),
            "https://203.0.113.10:8000/api/admin/v1",
        )


if __name__ == "__main__":
    unittest.main()
