"""检测并修复 github.com 的 DNS 污染/不可达问题（Windows）。

原理：国内 DNS 常把 github.com 解析到不可达 IP，但 api.github.com 正常。
脚本用 DoH 拿到权威 IP，再用 ``curl --resolve`` 逐个实测 HTTPS，
找出真正可用的 IP 后可写入 hosts（自动备份、可还原）。

用法：

    python scripts/check_github_network.py              # 只检测，不改动
    python scripts/check_github_network.py --apply      # 写入可用 IP 到 hosts
    python scripts/check_github_network.py --restore    # 从最近备份还原 hosts
"""

from __future__ import annotations

import argparse
import ctypes
import json
import shutil
import subprocess
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path

HOSTS = Path(r"C:\Windows\System32\drivers\etc\hosts")
BACKUP_DIR = Path(tempfile.gettempdir()) / "opencode" / "github-hosts-backups"
DOMAIN = "github.com"
FALLBACK_IPS = [
    "140.82.112.3",
    "140.82.113.3",
    "140.82.114.4",
    "140.82.121.4",
    "20.205.243.166",
    "20.247.166.64",
]


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def resolve_via_doh() -> list[str]:
    """从国内 DoH 服务获取权威 A 记录。"""
    found: list[str] = []
    for endpoint in ("https://doh.pub/dns-query", "https://dns.alidns.com/resolve"):
        try:
            url = f"{endpoint}?name={DOMAIN}&type=A"
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            payload = json.load(urllib.request.urlopen(request, timeout=8))
            for answer in payload.get("Answer", []):
                if answer.get("type") == 1 and answer.get("data") not in found:
                    found.append(answer["data"])
        except Exception:
            continue
    return found


def test_ip(ip: str, timeout: int = 6) -> bool:
    """用 curl --resolve 强制走指定 IP，检查 HTTPS 是否返回 200。"""
    command = [
        "curl.exe",
        "-sS",
        "-o",
        "NUL",
        "-w",
        "%{http_code}",
        "--resolve",
        f"{DOMAIN}:443:{ip}",
        "--connect-timeout",
        str(timeout),
        "--max-time",
        str(timeout + 2),
        f"https://{DOMAIN}",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 4)
        return result.stdout.strip() == "200"
    except (OSError, subprocess.TimeoutExpired):
        return False


def check() -> str | None:
    candidates = list(dict.fromkeys(resolve_via_doh() + FALLBACK_IPS))
    print(f"候选 IP（{len(candidates)} 个）：{', '.join(candidates)}")
    working: list[str] = []
    for ip in candidates:
        ok = test_ip(ip)
        print(f"  {ip:<18} {'可用' if ok else '不可用'}")
        if ok:
            working.append(ip)
    print(f"\n可用 IP：{working or '无'}")
    return working[0] if working else None


def apply_ip(ip: str) -> None:
    if not is_admin():
        raise SystemExit("写入 hosts 需要管理员权限，请在管理员 PowerShell 中运行。")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_DIR / f"hosts.{stamp}.bak"
    shutil.copy2(HOSTS, backup)
    lines = [line for line in HOSTS.read_text(encoding="utf-8").splitlines() if DOMAIN not in line]
    lines.append(f"{ip} {DOMAIN}")
    HOSTS.write_text("\n".join(lines) + "\n", encoding="ascii")
    subprocess.run(["ipconfig", "/flushdns"], capture_output=True)
    print(f"已写入 {ip} {DOMAIN}；备份：{backup}")


def restore() -> None:
    backups = sorted(BACKUP_DIR.glob("hosts.*.bak"))
    if not backups:
        raise SystemExit("没有找到 hosts 备份。")
    if not is_admin():
        raise SystemExit("还原 hosts 需要管理员权限。")
    shutil.copy2(backups[-1], HOSTS)
    subprocess.run(["ipconfig", "/flushdns"], capture_output=True)
    print(f"已从 {backups[-1]} 还原 hosts")


def main() -> int:
    parser = argparse.ArgumentParser(description="检测/修复 GitHub 网络加速")
    parser.add_argument("--apply", action="store_true", help="把可用 IP 写入 hosts（需管理员）")
    parser.add_argument("--restore", action="store_true", help="从最近备份还原 hosts")
    args = parser.parse_args()

    if args.restore:
        restore()
        return 0

    ip = check()
    if ip is None:
        print("未找到可用 IP；可稍后重试，或配置 SSH over 443 作为替代通道。")
        return 1
    if args.apply:
        apply_ip(ip)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
