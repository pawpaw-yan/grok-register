"""补 Build 工具：把 accounts.txt 里缺 Build 凭据的账号补齐并推送 grok2api。

流程与注册管道（account_outputs.add_token_to_grok2api_remote_pool，build 模式）完全一致：
  SSO --sso_to_auth_json（auth_code 优先，device 兜底）--> Build entry
      --> 落盘 output/build_auth/account_build-<email>.json
      --> multipart 推送 grok2api /accounts/import
      --> 追加 output/build_auth.txt（裸 RT）与 output/build_accounts.txt（邮箱----密码----RT）

用法:
  python backfill_build.py user@example.com   # 补指定账号（可多个，空格分隔）
  python backfill_build.py --missing                            # 自动补齐所有缺 Build 的账号
  python backfill_build.py --list                               # 只列出缺 Build 的账号，不动作
"""
# 直接以 `python <pkg>/<本文件>.py` 运行时，把项目根加入 sys.path
import os as _os, sys as _sys

_PROJECT_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _PROJECT_ROOT not in _sys.path:
    _sys.path.insert(0, _PROJECT_ROOT)

import argparse
import glob
import json
import os
import sys

from curl_cffi import requests  # noqa: F401  # 必须用 curl_cffi（multipart=CurlMime 只有它支持）

from registration import account_outputs
from core import app_config

ACCOUNTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "accounts.txt")
BUILD_AUTH_TXT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "build_auth.txt")
BUILD_ACCOUNTS_TXT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "build_accounts.txt")
BUILD_AUTH_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "build_auth")


def _log(message):
    print(str(message), flush=True)


def _log_exception(context, exc, log_callback=None):
    _log(f"[异常] {context}: {exc.__class__.__name__}: {exc}")


def _http_get(url, **kwargs):
    kwargs.setdefault("timeout", 30)
    return requests.get(url, **kwargs)


def _http_post(url, **kwargs):
    kwargs.setdefault("timeout", 120)
    return requests.post(url, **kwargs)


def _load_accounts():
    """accounts.txt -> {email: {"password": 真密码, "sso": 干净 sso}}。

    兼容 5 连杠行：密码以 '-' 结尾时分隔符被吃掉一位，sso 字段会带前导 '-'，
    在此还原（与 backfill/管道的 normalize 规则一致）。
    """
    result = {}
    with open(ACCOUNTS_FILE, encoding="utf-8") as handle:
        for line in handle:
            parts = line.strip().split("----")
            if len(parts) != 3 or not parts[0]:
                continue
            email, password, sso = parts
            if sso.startswith("-"):
                sso = sso.lstrip("-")
                password += "-"  # 被分隔符吞掉的密码尾巴
            result[email] = {"password": password, "sso": sso}
    return result


def _existing_build_emails():
    emails = set()
    if os.path.isfile(BUILD_ACCOUNTS_TXT):
        with open(BUILD_ACCOUNTS_TXT, encoding="utf-8") as handle:
            for line in handle:
                parts = line.strip().split("----")
                if parts and parts[0]:
                    emails.add(parts[0])
    for path in glob.glob(os.path.join(BUILD_AUTH_DIR, "*.json")):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            entry = (data.get("accounts") or [data])[0]
            email = str(entry.get("email") or "").strip()
            if email:
                emails.add(email)
        except Exception:
            continue
    return emails


def _append_outputs(email, password, refresh_token):
    with open(BUILD_AUTH_TXT, "a", encoding="utf-8") as handle:
        handle.write(refresh_token + "\n")
    with open(BUILD_ACCOUNTS_TXT, "a", encoding="utf-8") as handle:
        handle.write(f"{email}----{password}----{refresh_token}\n")


def backfill(emails, dry_run=False):
    accounts = _load_accounts()
    covered = _existing_build_emails()
    if not emails:
        emails = sorted(set(accounts) - covered)
    else:
        unknown = [e for e in emails if e not in accounts]
        if unknown:
            raise SystemExit(f"accounts.txt 中找不到: {', '.join(unknown)}")

    missing = [e for e in emails if e not in covered]
    already = [e for e in emails if e in covered]
    for email in already:
        _log(f"[=] {email} 已有 Build 凭据，跳过")
    if not missing:
        _log("[✓] 没有需要补的账号")
        return 0
    if dry_run:
        for email in missing:
            _log(f"[缺] {email}")
        _log(f"共 {len(missing)} 个缺 Build（--list 模式，未动作）")
        return 0

    ok, failed = 0, []
    for index, email in enumerate(missing, 1):
        info = accounts[email]
        _log(f"({index}/{len(missing)}) 转换 {email} ...")
        try:
            entry = account_outputs._convert_sso_to_build_entry(
                info["sso"], email=email, log_callback=_log
            )
            saved = account_outputs._save_build_entry_local(entry)
            account_outputs._push_build_entry_to_grok2api(entry, email=email, log_callback=_log)
            credential = (entry.get("accounts") or [entry])[0]
            refresh_token = str(credential.get("refresh_token") or "").strip()
            if refresh_token:
                _append_outputs(email, info["password"], refresh_token)
            _log(f"[+] 完成 {email}（落盘 {saved}）")
            ok += 1
        except Exception as exc:
            _log_exception(f"补 Build 失败 {email}", exc)
            failed.append(email)
    _log(f"[✓] 成功 {ok} / {len(missing)}" + (f"，失败 {len(failed)}: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description="补 Build 凭据并推送 grok2api")
    parser.add_argument("emails", nargs="*", help="要补的账号邮箱（accounts.txt 中的）")
    parser.add_argument("--missing", action="store_true", help="自动补齐所有缺 Build 的账号")
    parser.add_argument("--list", action="store_true", help="只列出缺 Build 的账号")
    args = parser.parse_args()

    config = app_config.load_config()
    account_outputs.configure_token_runtime(
        config, _http_get, _http_post, _log_exception,
        compatibility_error=RuntimeError, request_error=RuntimeError,
    )

    if args.list:
        accounts = _load_accounts()
        missing = sorted(set(accounts) - _existing_build_emails())
        for email in missing:
            print(email)
        print(f"-- 共 {len(missing)} 个缺 Build / 账号总数 {len(accounts)}", file=sys.stderr)
        return 0

    emails = args.emails
    if args.missing:
        emails = []
    elif not emails:
        parser.error("请给邮箱、--missing 或 --list")
    return backfill(emails, dry_run=args.list)


if __name__ == "__main__":
    sys.exit(main())
