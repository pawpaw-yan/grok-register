"""config_tui 的纯函数回归：按模式过滤分组、脱敏、输入解析。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import app_config
import config_tui


def _titles(cfg):
    return {title: keys for title, keys in config_tui.module_groups(cfg)}


def test_visible_keys_have_no_duplicates():
    cfg = dict(app_config.DEFAULT_CONFIG)
    cfg.update({"email_provider": "cloudflare", "proxy_mode": "pool", "grok2api_ingest_mode": "build"})
    for _, keys in config_tui.module_groups(cfg):
        assert len(keys) == len(set(keys)), "同屏可见键不能重复"


def test_email_module_filters_by_provider():
    cfg = dict(app_config.DEFAULT_CONFIG)
    cfg["email_provider"] = "cloudflare"
    mail = _titles(cfg)["邮箱配置"]
    for key in ("cloudflare_api_base", "cloudflare_api_key", "cloudflare_auth_mode",
                "cloudflare_path_messages", "email_provider", "defaultDomains"):
        assert key in mail, f"cloudflare 模式应显示 {key}"
    for hidden in ("duckmail_api_key", "yyds_jwt", "cloudmail_api_base", "outlook_accounts_file"):
        assert hidden not in mail, f"cloudflare 模式不应显示 {hidden}"

    cfg["email_provider"] = "duckmail"
    mail = _titles(cfg)["邮箱配置"]
    assert "duckmail_api_key" in mail
    assert "cloudflare_api_base" not in mail


def test_proxy_module_filters_by_mode():
    cfg = dict(app_config.DEFAULT_CONFIG)
    cfg["proxy_mode"] = "pool"
    proxy = _titles(cfg)["代理配置"]
    assert "proxy_pool_file" in proxy and "proxy_singbox_path" in proxy
    assert "proxy" not in proxy

    cfg["proxy_mode"] = "single"
    proxy = _titles(cfg)["代理配置"]
    assert "proxy" in proxy and "proxy_fallback" in proxy
    assert "proxy_pool_file" not in proxy

    cfg["proxy_mode"] = "direct"
    assert _titles(cfg)["代理配置"] == ["proxy_mode"]


def test_grok2api_local_and_remote_gates():
    cfg = dict(app_config.DEFAULT_CONFIG)
    cfg["grok2api_auto_add_local"] = False
    assert _titles(cfg)["本地 grok2api"] == ["grok2api_auto_add_local"]

    cfg["grok2api_auto_add_local"] = True
    local = _titles(cfg)["本地 grok2api"]
    assert "grok2api_local_token_file" in local and "grok2api_pool_name" in local

    cfg["grok2api_auto_add_remote"] = False
    assert _titles(cfg)["远程 grok2api / Build 入池"] == ["grok2api_auto_add_remote"]

    cfg["grok2api_auto_add_remote"] = True
    cfg["grok2api_ingest_mode"] = "web"
    remote = _titles(cfg)["远程 grok2api / Build 入池"]
    assert "grok2api_remote_base" in remote
    for build_key in ("grok2api_build_auth_dir", "grok2api_build_fallback_web", "grok2api_build_convert_proxy"):
        assert build_key not in remote, f"web 模式不应显示 {build_key}"

    cfg["grok2api_ingest_mode"] = "build"
    remote = _titles(cfg)["远程 grok2api / Build 入池"]
    for build_key in ("grok2api_build_auth_dir", "grok2api_build_fallback_web", "grok2api_build_convert_proxy"):
        assert build_key in remote, f"build 模式应显示 {build_key}"

    cfg["grok2api_ingest_mode"] = "both"
    remote = _titles(cfg)["远程 grok2api / Build 入池"]
    for build_key in ("grok2api_build_auth_dir", "grok2api_build_fallback_web", "grok2api_build_convert_proxy"):
        assert build_key in remote, f"both 模式应显示 {build_key}"


def test_cpa_and_base_conditional_keys():
    cfg = dict(app_config.DEFAULT_CONFIG)
    cpa = _titles(cfg)["CPA 配置"]
    assert "cpa_hotload_dir" not in cpa
    cfg["cpa_copy_to_hotload"] = True
    assert "cpa_hotload_dir" in _titles(cfg)["CPA 配置"]

    cfg["multi_thread_enabled"] = False
    base = _titles(cfg)["注册基础配置"]
    assert "multi_thread_workers" not in base
    cfg["sso_risk_gate_enabled"] = False
    base = _titles(cfg)["注册基础配置"]
    assert "sso_risk_rejected_file" not in base


def test_module_groups_claim_all_known_keys():
    """所有默认键都被模块认领（可见或被条件隐藏），不落入「其他配置」。"""
    cfg = dict(app_config.DEFAULT_CONFIG)
    rest = dict(config_tui.module_groups(cfg)).get("其他配置", [])
    assert rest == [], f"默认配置键不应落到其他配置: {rest}"


def test_mask_value_secrets():
    assert config_tui.mask_value("duckmail_api_key", "") == "(未设置)"
    masked = config_tui.mask_value("grok2api_remote_admin_password", "super-secret-value")
    assert masked.startswith("supe") and "***" in masked and "secret-value" not in masked
    assert config_tui.mask_value("register_count", 3) == "3"
    assert config_tui.mask_value("enable_nsfw", True) == "true"
    assert config_tui.mask_value("proxy", "") == "(空)"


def test_parse_input_bool():
    for raw, expect in (("1", True), ("true", True), ("是", True), ("0", False), ("no", False), ("否", False)):
        ok, value = config_tui.parse_input(raw, "enable_nsfw", True)
        assert ok and value is expect, raw
    ok, message = config_tui.parse_input("maybe", "enable_nsfw", True)
    assert not ok and message


def test_parse_input_int_and_str():
    ok, value = config_tui.parse_input(" 42 ", "register_count", 1)
    assert ok and value == 42
    ok, message = config_tui.parse_input("abc", "register_count", 1)
    assert not ok and message
    ok, value = config_tui.parse_input("  hello ", "proxy", "")
    assert ok and value == "hello"
    ok, value = config_tui.parse_input("  spaced  ", "user_agent", "ua")
    assert ok and value == "  spaced  ", "user_agent 不做 strip"
    ok, value = config_tui.parse_input("清空", "proxy", "http://x")
    assert ok and value == ""
    ok, value = config_tui.parse_input("none", "cloudflare_auth_mode", "none")
    assert ok and value == "none", "none 是合法字面值，不应被清空语义劫持"
