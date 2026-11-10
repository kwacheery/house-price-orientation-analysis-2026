"""W0 环境自检。

检查内容：
1. Python 版本与虚拟环境
2. 项目目录结构是否完整
3. 核心依赖能否导入（加分项依赖缺失只告警）
4. SQLite 是否可读写
5. Playwright Chromium 能否启动

用法：
    python scripts/check_env.py
    python scripts/check_env.py --skip-browser
    python scripts/check_env.py --json reports/env_check.json

退出码：
    0  全部必需项通过
    1  存在必需项失败
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import platform
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

MIN_PYTHON = (3, 11)
VERIFIED_PYTHON = "3.14.5"

# (导入名, 发行包名, 用途, 是否必需)
PACKAGES = [
    ("numpy", "numpy", "数值计算", True),
    ("pandas", "pandas", "数据处理", True),
    ("scipy", "scipy", "统计计算", True),
    ("sklearn", "scikit-learn", "建模", True),
    ("statsmodels", "statsmodels", "回归与溢价分析", True),
    ("joblib", "joblib", "模型持久化", True),
    ("requests", "requests", "HTTP 采集", True),
    ("bs4", "beautifulsoup4", "HTML 解析", True),
    ("lxml", "lxml", "HTML 解析后端", True),
    ("playwright", "playwright", "动态页面采集", True),
    ("fastapi", "fastapi", "Web 后端", True),
    ("uvicorn", "uvicorn", "Web 服务", True),
    ("matplotlib", "matplotlib", "图表", True),
    ("yaml", "PyYAML", "配置文件", True),
    ("pytest", "pytest", "单元测试", True),
    ("xgboost", "xgboost", "加分项模型", False),
    ("lightgbm", "lightgbm", "加分项模型", False),
]

REQUIRED_DIRS = [
    ".github/workflows",
    "config",
    "crawler",
    "data/db",
    "data/processed",
    "data/raw",
    "data/samples",
    "docs",
    "models",
    "models/artifacts",
    "notebooks",
    "pipeline",
    "reports",
    "reports/figures",
    "scripts",
    "tests",
    "web/backend",
    "web/frontend",
    "web/frontend/data",
]

STATUS_PASS = "通过"
STATUS_FAIL = "失败"
STATUS_SKIP = "跳过"


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    required: bool = True
    hint: str = ""


def package_version(dist_name: str) -> str:
    try:
        return importlib.metadata.version(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def check_python() -> CheckResult:
    version = sys.version_info
    version_text = f"{version.major}.{version.minor}.{version.micro}"
    in_venv = sys.prefix != sys.base_prefix
    venv_text = f"venv={sys.prefix}" if in_venv else "未在虚拟环境中运行"

    if version[:2] < MIN_PYTHON:
        return CheckResult(
            "Python 版本",
            STATUS_FAIL,
            f"{version_text}（要求 >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}）",
            hint="安装 Python 3.11+ 后重建 .venv",
        )

    note = "" if version_text == VERIFIED_PYTHON else f"（已验证组合为 {VERIFIED_PYTHON}）"
    return CheckResult("Python 版本", STATUS_PASS, f"{version_text}{note}，{venv_text}")


def check_dirs() -> list[CheckResult]:
    results = []
    for rel in REQUIRED_DIRS:
        path = PROJECT_ROOT / rel
        exists = path.is_dir()
        results.append(
            CheckResult(
                f"目录 {rel}",
                STATUS_PASS if exists else STATUS_FAIL,
                "存在" if exists else "缺失",
                hint="" if exists else "从仓库根目录运行，或补齐目录结构",
            )
        )
    return results


def check_package(import_name: str, dist_name: str, purpose: str, required: bool) -> CheckResult:
    label = f"依赖 {dist_name}"
    try:
        module = importlib.import_module(import_name)
    except Exception as exc:  # noqa: BLE001 - 自检需要报告任何导入错误
        return CheckResult(
            label,
            STATUS_FAIL if required else STATUS_SKIP,
            f"{purpose}：导入失败（{type(exc).__name__}: {exc}）",
            required=required,
            hint="" if required else "加分项，可忽略；需要时 pip install -r requirements.txt",
        )

    version = getattr(module, "__version__", None) or package_version(dist_name)
    return CheckResult(label, STATUS_PASS, f"{purpose}：{version}", required=required)


def check_sqlite() -> CheckResult:
    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO t (value) VALUES (?)", ("ok",))
            row = conn.execute("SELECT value FROM t WHERE id = 1").fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "SQLite",
            STATUS_FAIL,
            f"读写失败（{type(exc).__name__}: {exc}）",
            hint="检查 Python 安装是否包含 sqlite3",
        )
    return CheckResult("SQLite", STATUS_PASS, f"{sqlite3.sqlite_version}，内存库读写正常（返回 {row[0]!r}）")


def check_playwright(skip: bool) -> CheckResult:
    if skip:
        return CheckResult("Playwright Chromium", STATUS_SKIP, "按参数跳过", required=False)

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "Playwright Chromium",
            STATUS_FAIL,
            f"导入 playwright 失败（{type(exc).__name__}: {exc}）",
            hint="pip install -r requirements.txt",
        )

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content("<h1 id='t'>ok</h1>")
                text = page.inner_text("#t")
                version = browser.version
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "Playwright Chromium",
            STATUS_FAIL,
            f"浏览器启动失败（{type(exc).__name__}: {exc}）",
            hint="运行 python -m playwright install chromium",
        )

    return CheckResult("Playwright Chromium", STATUS_PASS, f"{version}，页面渲染正常（{text}）")


def print_result(result: CheckResult) -> None:
    print(f"[{result.status}] {result.name}: {result.detail}")
    if result.status != STATUS_PASS and result.hint:
        print(f"         提示：{result.hint}")


def main() -> int:
    parser = argparse.ArgumentParser(description="W0 环境自检")
    parser.add_argument("--skip-browser", action="store_true", help="跳过 Playwright 浏览器启动检查")
    parser.add_argument("--json", type=Path, help="将自检结果写入 JSON 文件")
    args = parser.parse_args()

    print("=" * 72)
    print("城市房价预测与朝向溢价分析 - 环境自检")
    print(f"时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"解释器：{sys.executable}")
    print(f"平台：{platform.platform()} / {platform.machine()}")
    print(f"项目根目录：{PROJECT_ROOT}")
    print("=" * 72)

    results: list[CheckResult] = [check_python()]
    results.extend(check_dirs())
    results.extend(check_package(*item) for item in PACKAGES)
    results.append(check_sqlite())
    results.append(check_playwright(args.skip_browser))

    for result in results:
        print_result(result)

    failed = [r for r in results if r.status == STATUS_FAIL]
    skipped = [r for r in results if r.status == STATUS_SKIP]
    passed = [r for r in results if r.status == STATUS_PASS]

    print("-" * 72)
    print(f"汇总：通过 {len(passed)} 项，失败 {len(failed)} 项，跳过 {len(skipped)} 项")

    if failed:
        print()
        print("未通过项：")
        for result in failed:
            print(f"  - {result.name}: {result.detail}")
        print("环境自检未通过，请按上方提示修复后重试。")
    else:
        print("环境自检通过，可以进入 W1 试点采集。")

    if args.json:
        payload = {
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "project_root": str(PROJECT_ROOT),
            "summary": {"passed": len(passed), "failed": len(failed), "skipped": len(skipped)},
            "checks": [asdict(r) for r in results],
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已写入 {args.json}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
