"""配置中心 TUI：分模块查看与修改 config.json，并可就地调起 CLI 注册。

纯标准库实现，SSH 终端 / Windows / Linux 均可运行：

    python config_tui.py

入口脚本：Windows 用 register.bat，Linux/A1 用 register.sh（支持模块子命令）。

模块设计：带模式的模块（邮箱/代理/grok2api/CPA）先显示模式开关，
选定模式后只渲染该模式相关的配置项（scopes + 条件键）。
"""
import json
import os
import subprocess
import sys

from core import app_config

ROOT = os.path.dirname(os.path.abspath(__file__))
CLI_ENTRY = os.path.join(ROOT, "grok_register_ttk.py")

SECRET_KEYS = {
    "duckmail_api_key",
    "yyds_api_key",
    "yyds_jwt",
    "cloudflare_api_key",
    "cloudflare_site_password",
    "cloudmail_public_token",
    "grok2api_remote_app_key",
    "grok2api_remote_admin_password",
    "display_vnc_password",
}

_POOL_KEYS = (
    ("proxy_pool_file", None),
    ("proxy_pool_subscription_url", None),
    ("proxy_pool_subscription_proxy", None),
    ("proxy_pool_subscription_public_only", None),
    ("proxy_pool_endpoint_mode", None),
    ("proxy_pool_refresh_interval_sec", None),
    ("proxy_pool_probe_interval_sec", None),
    ("proxy_pool_probe_timeout_sec", None),
    ("proxy_pool_probe_provider", None),
    ("proxy_pool_probe_dual_stack", None),
    ("proxy_pool_max_concurrent_per_node", None),
    ("proxy_pool_acquire_timeout_sec", None),
    ("proxy_pool_persist_health", None),
    ("proxy_pool_state_file", None),
    ("proxy_pool_preflight_enabled", None),
    ("proxy_protocol_backend", None),
    ("proxy_singbox_path", None),
    ("proxy_protocol_start_timeout_sec", None),
    ("proxy_runtime_idle_ttl_sec", None),
    ("proxy_runtime_cache_max", None),
)

# 模块规格：
#   gate   —— 模式开关键，永远显示在首位；其值决定 scopes 中哪组键可见
#   keys   —— (键, 条件) 列表；条件 {依赖键: (允许值...)} 不满足则隐藏
#   scopes —— {模式值: (键, 条件) 列表}
#   hint   —— 屏幕顶部的操作提示
MODULE_SPECS = (
    ("注册基础配置", {
        "keys": (
            ("register_count", None),
            ("account_interval", None),
            ("enable_nsfw", None),
            ("sso_risk_gate_enabled", None),
            ("sso_risk_rejected_file", {"sso_risk_gate_enabled": (True,)}),
            ("hidden_window", None),
            ("multi_thread_enabled", None),
            ("multi_thread_workers", {"multi_thread_enabled": (True,)}),
            ("user_agent", None),
            ("browser_path", None),
            ("browser_preset", None),
            ("turnstile_screen_patch", None),
            ("display_backend", None),
            ("display_num", {"display_backend": ("xvfb", "auto")}),
            ("display_screen", {"display_backend": ("xvfb", "auto")}),
            ("display_wait_sec", {"display_backend": ("xvfb", "auto")}),
            ("display_vnc_enabled", None),
            ("display_vnc_port", {"display_vnc_enabled": (True,)}),
            ("display_vnc_listen", {"display_vnc_enabled": (True,)}),
            ("display_vnc_viewonly", {"display_vnc_enabled": (True,)}),
            ("display_vnc_password", {"display_vnc_enabled": (True,)}),
        ),
    }),
    ("邮箱配置", {
        "gate": "email_provider",
        "hint": "先切换 email_provider，下方只显示该 provider 的相关配置",
        "keys": (
            ("defaultDomains", None),
        ),
        "scopes": {
            "duckmail": (("duckmail_api_key", None),),
            "yyds": (("yyds_api_key", None), ("yyds_jwt", None)),
            "cloudflare": (
                ("cloudflare_api_base", None),
                ("cloudflare_api_key", None),
                ("cloudflare_site_password", None),
                ("cloudflare_auth_mode", None),
                ("cloudflare_path_domains", None),
                ("cloudflare_path_accounts", None),
                ("cloudflare_path_token", None),
                ("cloudflare_path_messages", None),
            ),
            "cloudmail": (
                ("cloudmail_api_base", None),
                ("cloudmail_public_token", None),
                ("cloudmail_domains", None),
                ("cloudmail_path_messages", None),
            ),
            "outlook": (("outlook_accounts_file", None),),
        },
    }),
    ("代理配置", {
        "gate": "proxy_mode",
        "hint": "auto=自动(可配单代理+回退) direct=直连 single=单代理 pool=代理池",
        "keys": (),
        "scopes": {
            "auto": (("proxy", None), ("proxy_fallback", None)),
            "single": (("proxy", None), ("proxy_fallback", None)),
            "direct": (),
            "pool": _POOL_KEYS,
        },
    }),
    ("本地 grok2api", {
        "gate": "grok2api_auto_add_local",
        "hint": "开启后显示本地池配置",
        "keys": (),
        "scopes": {
            True: (
                ("grok2api_local_token_file", None),
                ("grok2api_pool_name", None),
                ("grok2api_allow_legacy_full_save", None),
            ),
            False: (),
        },
    }),
    ("远程 grok2api / Build 入池", {
        "gate": "grok2api_auto_add_remote",
        "hint": "凭据二选一：remote_app_key 或 admin_username + admin_password；"
                "ingest_mode: web=仅 Web SSO 池 / build=仅 Build / both=两者都入；"
                "build/both 时显示 Build 专属项",
        "keys": (
            ("grok2api_remote_base", {"grok2api_auto_add_remote": (True,)}),
            ("grok2api_remote_app_key", {"grok2api_auto_add_remote": (True,)}),
            ("grok2api_remote_admin_username", {"grok2api_auto_add_remote": (True,)}),
            ("grok2api_remote_admin_password", {"grok2api_auto_add_remote": (True,)}),
            ("grok2api_remote_allow_http", {"grok2api_auto_add_remote": (True,)}),
            ("grok2api_ingest_mode", {"grok2api_auto_add_remote": (True,)}),
            ("grok2api_build_auth_dir", {"grok2api_auto_add_remote": (True,), "grok2api_ingest_mode": ("build", "both")}),
            ("grok2api_build_fallback_web", {"grok2api_auto_add_remote": (True,), "grok2api_ingest_mode": ("build", "both")}),
            ("grok2api_build_convert_proxy", {"grok2api_auto_add_remote": (True,), "grok2api_ingest_mode": ("build", "both")}),
        ),
        "scopes": {},
    }),
    ("CPA 配置", {
        "gate": "cpa_export_enabled",
        "hint": "cpa_copy_to_hotload=true 时显示 cpa_hotload_dir",
        "keys": (
            ("cpa_base_url", {"cpa_export_enabled": (True,)}),
            ("cpa_auth_dir", {"cpa_export_enabled": (True,)}),
            ("cpa_proxy", {"cpa_export_enabled": (True,)}),
            ("cpa_headless", {"cpa_export_enabled": (True,)}),
            ("cpa_force_standalone", {"cpa_export_enabled": (True,)}),
            ("cpa_mint_timeout_sec", {"cpa_export_enabled": (True,)}),
            ("cpa_mint_cookie_inject", {"cpa_export_enabled": (True,)}),
            ("cpa_oidc_request_timeout_sec", {"cpa_export_enabled": (True,)}),
            ("cpa_oidc_poll_timeout_sec", {"cpa_export_enabled": (True,)}),
            ("cpa_copy_to_hotload", {"cpa_export_enabled": (True,)}),
            ("cpa_hotload_dir", {"cpa_export_enabled": (True,), "cpa_copy_to_hotload": (True,)}),
            ("api_reverse_tools", {"cpa_export_enabled": (True,)}),
        ),
        "scopes": {},
    }),
)


def _cond_ok(cond, cfg):
    if not cond:
        return True
    return all(cfg.get(dep) in allowed for dep, allowed in cond.items())


def _all_spec_keys():
    claimed = set()
    for _, spec in MODULE_SPECS:
        if spec.get("gate"):
            claimed.add(spec["gate"])
        for key, _ in spec.get("keys", ()):
            claimed.add(key)
        for scope_keys in spec.get("scopes", {}).values():
            for key, _ in scope_keys:
                claimed.add(key)
    return claimed


def _visible_from_spec(spec, cfg):
    """当前 cfg 下该模块可见的键（模式开关在首位）。"""
    keys = []
    gate = spec.get("gate")
    if gate:
        keys.append(gate)
    for key, cond in spec.get("keys", ()):
        if key in cfg and key not in keys and _cond_ok(cond, cfg):
            keys.append(key)
    if gate:
        for key, cond in spec.get("scopes", {}).get(cfg.get(gate), ()):
            if key in cfg and key not in keys and _cond_ok(cond, cfg):
                keys.append(key)
    return keys


def module_specs(cfg):
    """[(标题, 可见键, spec)]；未被任何模块认领的键进「其他配置」。"""
    out = []
    for title, spec in MODULE_SPECS:
        out.append((title, _visible_from_spec(spec, cfg), spec))
    claimed = _all_spec_keys()
    rest = [k for k in cfg if k not in claimed]
    if rest:
        out.append(("其他配置", rest, {"keys": tuple((k, None) for k in rest)}))
    return out


def module_groups(cfg):
    """兼容入口：[(标题, 可见键)]。"""
    return [(title, keys) for title, keys, _ in module_specs(cfg)]


def mask_value(key, value):
    """展示值；机密键只露前 4 位。"""
    if key in SECRET_KEYS:
        text = str(value)
        if not text:
            return "(未设置)"
        return text[:4] + "***" + f"(len={len(text)})"
    if isinstance(value, bool):
        return "true" if value else "false"
    if value == "":
        return "(空)"
    return str(value)


def parse_input(raw, key, current):
    """把用户输入解析成与 current 同类型的新值；返回 (ok, value 或 错误文本)。

    空白输入由调用方先行拦截（= 保持原值）。
    """
    if isinstance(current, bool):
        low = raw.strip().lower()
        if low in ("1", "true", "y", "yes", "是", "开"):
            return True, True
        if low in ("0", "false", "n", "no", "否", "关"):
            return True, False
        return False, "布尔值请输入 true/false（或 是/否、1/0）"
    if isinstance(current, int) and not isinstance(current, bool):
        try:
            return True, int(raw.strip())
        except ValueError:
            return False, "整数请输入数字"
    value = raw if key == "user_agent" else raw.strip()
    if raw.strip() in ("清空",):
        value = ""
    return True, value


def load_config_tolerant():
    """优先走 app_config.load_config；config.json 已损坏时退回 裸 JSON + 默认值 合并，
    让 TUI 兼任修复工具（保存时才做完整校验）。"""
    try:
        return dict(app_config.load_config())
    except app_config.ConfigError as exc:
        print(f"[!] 当前 config.json 校验未通过：{exc}")
        print("[!] 进入修复模式：显示 默认值+现文件 合并结果，保存时会做完整校验")
        merged = dict(app_config.DEFAULT_CONFIG)
        if os.path.exists(app_config.CONFIG_FILE):
            try:
                with open(app_config.CONFIG_FILE, encoding="utf-8") as handle:
                    merged.update(json.load(handle))
            except Exception as load_exc:
                print(f"[!] 读取原始文件失败，仅使用默认值: {load_exc}")
        return merged


def persist(cfg):
    """校验并落盘；失败抛 ConfigError（调用方回滚内存值）。"""
    app_config._replace_config(cfg)
    app_config.save_config()


def edit_module(title, spec, cfg):
    gate = spec.get("gate")
    while True:
        keys = _visible_from_spec(spec, cfg)
        print(f"\n---- {title} ----")
        if gate:
            print(f"  [模式] {gate} = {mask_value(gate, cfg.get(gate))}")
        if spec.get("hint"):
            print(f"  * {spec['hint']}")
        for index, key in enumerate(keys, 1):
            tag = "  [模式开关]" if gate and key == gate else ""
            print(f"  {index:>2}. {key:<38} = {mask_value(key, cfg.get(key))}{tag}")
        print("  输入编号修改对应值；直接回车返回上级菜单")
        try:
            choice = input("选择> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not choice:
            return
        if not choice.isdigit() or not 1 <= int(choice) <= len(keys):
            print("[!] 无效编号")
            continue
        key = keys[int(choice) - 1]
        current = cfg.get(key)
        hint = "布尔值" if isinstance(current, bool) else ("整数" if isinstance(current, int) else "字符串")
        print(f"修改 {key}\n  当前值: {mask_value(key, current)}  （{hint}；回车保持不变；字符串输入 清空 置空）")
        try:
            raw = input("  新值> ")
        except (EOFError, KeyboardInterrupt):
            print()
            continue
        if raw.strip() == "" and key != "user_agent":
            print("  (= 保持原值)")
            continue
        ok, value = parse_input(raw, key, current)
        if not ok:
            print(f"[!] {value}")
            continue
        original = cfg.get(key)
        cfg[key] = value
        try:
            persist(cfg)
        except app_config.ConfigError as exc:
            cfg[key] = original
            print(f"[!] 保存失败（已还原）: {exc}")
            continue
        print(f"[✓] 已保存: {key} = {mask_value(key, value)}")


def run_registration(cfg, preset_count=None):
    count = str(preset_count or cfg.get("register_count", 1) or 1)
    if preset_count is None:
        try:
            raw = input(f"注册数量（回车 = 配置值 {count}）> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if raw:
            if not raw.isdigit() or int(raw) <= 0:
                print("[!] 数量必须是正整数")
                return
            count = raw
    print(f"\n[*] 启动 CLI 注册（数量 {count}）；提示符出现后输入 start 开始，Ctrl+C 可停止\n")
    try:
        subprocess.run([sys.executable, CLI_ENTRY, "cli", count], cwd=ROOT)
    except KeyboardInterrupt:
        print("\n[*] 已中断，返回菜单")
    except Exception as exc:
        print(f"[!] 启动失败: {exc}")
    try:
        input("\n按回车返回菜单...")
    except (EOFError, KeyboardInterrupt):
        print()


def show_full_config(cfg):
    print(f"\n---- 完整配置（{app_config.CONFIG_FILE}，机密已脱敏） ----")
    keys = sorted(cfg)
    per_page = 28
    for index, key in enumerate(keys):
        print(f"  {key:<40} = {mask_value(key, cfg.get(key))}")
        if (index + 1) % per_page == 0 and index + 1 < len(keys):
            try:
                input("  -- 回车继续 --")
            except (EOFError, KeyboardInterrupt):
                print()
                return


MENU = (
    ("运行注册（CLI，可临时指定数量）", None),
    ("注册基础配置", "注册基础配置"),
    ("邮箱配置", "邮箱配置"),
    ("代理配置", "代理配置"),
    ("本地 grok2api", "本地 grok2api"),
    ("远程 grok2api / Build 入池", "远程 grok2api / Build 入池"),
    ("CPA 配置", "CPA 配置"),
    ("查看完整配置（只读）", None),
)

# 命令行子入口（register.bat / register.sh 的参数）：
#   run [数量] / show / base|mail|proxy|local|remote|cpa（中英文别名均可）
MODULE_ALIASES = {
    "base": "注册基础配置", "basic": "注册基础配置", "基础": "注册基础配置",
    "mail": "邮箱配置", "email": "邮箱配置", "邮箱": "邮箱配置",
    "proxy": "代理配置", "代理": "代理配置",
    "local": "本地 grok2api", "本地": "本地 grok2api",
    "remote": "远程 grok2api / Build 入池", "build": "远程 grok2api / Build 入池", "远程": "远程 grok2api / Build 入池",
    "cpa": "CPA 配置",
}

USAGE = (
    "用法: python config_tui.py [子命令]\n"
    "  （无参数）  主菜单\n"
    "  run [数量]  直接启动 CLI 注册\n"
    "  show        查看完整配置（脱敏）\n"
    "  base|mail|proxy|local|remote|cpa    直接进入对应模块配置"
)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg = load_config_tolerant()
    if argv:
        command = argv[0].lower()
        if command == "run":
            count = argv[1] if len(argv) > 1 else None
            if count is not None and (not count.isdigit() or int(count) <= 0):
                print("[!] 数量必须是正整数")
                return 2
            run_registration(cfg, preset_count=count)
            return 0
        if command in ("show", "查看"):
            show_full_config(cfg)
            return 0
        title = MODULE_ALIASES.get(command)
        if title:
            spec = dict((t, s) for t, _, s in module_specs(cfg)).get(title)
            if spec:
                edit_module(title, spec, cfg)
            return 0
        print(USAGE)
        return 2
    specs = {title: spec for title, _, spec in module_specs(cfg)}
    while True:
        print("\n" + "=" * 58)
        print("  grok-register 配置中心")
        print(f"  配置文件: {app_config.CONFIG_FILE}")
        print("=" * 58)
        for index, (label, _) in enumerate(MENU, 1):
            print(f"  [{index}] {label}")
        print("  [0] 退出（每次修改均已即时保存）")
        try:
            choice = input("选择> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice == "0":
            return 0
        if choice == "1":
            run_registration(cfg)
            continue
        if choice == str(len(MENU)):
            show_full_config(cfg)
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(MENU):
            title = MENU[int(choice) - 1][1]
            if title and title in specs:
                edit_module(title, specs[title], cfg)
                continue
        print("[!] 无效选择")


if __name__ == "__main__":
    sys.exit(main())
