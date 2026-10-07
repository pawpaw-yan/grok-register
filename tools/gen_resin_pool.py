#!/usr/bin/env python3
# Engine: Python 3.10+ | Language: zh
# 用途：生成 Resin 轮换网关的节点池文件（grok-register 代理池直接消费）
# Run: python gen_resin_pool.py [--token ';hCURd1^,v'] [--count 1000] [--out output/resin-node.txt]
# Deps: 无（纯标准库）
#
# Resin 寻址（实测 2026-10-06）：
#   socks5h://平台.账号:密码@resin.aurora.bot.cd:8444
#   - 用户名 `平台.账号`：`Default` 单独用 = 每次随机出口；
#     `Default.xxx` = 粘性身份，一账号固定一出口 IP（粘到坏出口则持续坏，靠池探活剪除）
#   - 密码含 URL 特殊字符（如 ; ^ ,），本脚本自动百分号转义
#   - 偶发 SOCKS5 general failure（上游取出口瞬时失败），重试或换身份即可

import argparse
import urllib.parse
from pathlib import Path

DEFAULT_HOST = "resin.aurora.bot.cd"
DEFAULT_PORT = 8444
DEFAULT_PREFIX = "Default"
DEFAULT_TOKEN = ";hCURd1^,v"


def main():
    parser = argparse.ArgumentParser(description="生成 Resin 节点池文件")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Resin 网关主机（默认 {DEFAULT_HOST}）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"网关端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="连接密码（自动百分号转义）")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help=f"用户名平台前缀（默认 {DEFAULT_PREFIX}）")
    parser.add_argument("--count", type=int, default=1000, help="生成节点数（默认 1000）")
    parser.add_argument("--start", type=int, default=1, help="身份编号起始（默认 1）")
    parser.add_argument("--out", default="output/resin-node.txt", help="输出文件（默认 output/resin-node.txt）")
    args = parser.parse_args()

    token_enc = urllib.parse.quote(args.token, safe="")
    lines = []
    for i in range(args.start, args.start + args.count):
        account = f"resin-{i:04d}"
        lines.append(f"socks5h://{args.prefix}.{account}:{token_enc}@{args.host}:{args.port}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[+] 已生成 {args.count} 个粘性身份节点 -> {out.resolve()}")
    print(f"    身份范围 resin-{args.start:04d} .. resin-{args.start + args.count - 1:04d}")
    print("[!] 注意：粘到坏出口的身份会持续失败，属正常现象，交给池探活剪除。")
    print("    若重跑生成请换 --start 区间，避免复用已知坏身份名。")


if __name__ == "__main__":
    main()
