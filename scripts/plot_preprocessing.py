"""生成 W2 预处理图表。

产物：
- ``reports/figures/missing_before_after.png``：关键字段清洗前缺失 vs 清洗后“未知”计数
- ``reports/figures/outlier_boxplot.png``：广州各区县单价箱线图（全部样本 vs 剔除异常值）

用法：
    python scripts/plot_preprocessing.py
    python scripts/plot_preprocessing.py --day 2026-11-10
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

CLEAN_PATH = PROJECT_ROOT / "data" / "processed" / "clean.csv"
FIGURE_DIR = PROJECT_ROOT / "reports" / "figures"

# 关键字段：原始缺失 vs 清洗后显式“未知”
FIELDS = [
    ("district", "区县"),
    ("build_year", "建成年份"),
    ("decoration", "装修"),
    ("elevator", "电梯"),
    ("floor_level", "楼层"),
    ("orientation", "朝向"),
]

UNKNOWN_VALUES = {"未知", "未知小区", ""}


def configure_fonts() -> None:
    available = {font.name for font in font_manager.fontManager.ttflist}
    for name in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "PingFang SC"):
        if name in available:
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            break
    plt.rcParams["axes.unicode_minus"] = False


def read_raw_missing(raw_root: Path, day: str | None) -> tuple[Counter[str], int]:
    pattern = f"*/*/{day}.jsonl" if day else "*/*/*.jsonl"
    counts: Counter[str] = Counter()
    total = 0
    seen: set[tuple[str, str]] = set()
    for path in sorted(raw_root.glob(pattern)):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = (str(record.get("source")), str(record.get("url")))
                if key in seen:
                    continue
                seen.add(key)
                total += 1
                for field, _ in FIELDS:
                    value = record.get(field)
                    if value in (None, "") or str(value).strip() in UNKNOWN_VALUES:
                        counts[field] += 1
    return counts, total


def read_clean(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def clean_unknown(rows: list[dict[str, str]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        for field, _ in FIELDS:
            value = (row.get(field) or "").strip()
            if value in UNKNOWN_VALUES:
                counts[field] += 1
        if row.get("age_missing") == "1" and (row.get("build_year") or "").strip():
            counts["build_year"] += 1
    return counts


def plot_missing(raw_counts: Counter[str], clean_counts: Counter[str], path: Path) -> None:
    labels = [label for _, label in FIELDS]
    before = [raw_counts[field] for field, _ in FIELDS]
    after = [clean_counts[field] for field, _ in FIELDS]
    x = range(len(labels))
    width = 0.38

    figure, axis = plt.subplots(figsize=(9, 4.8), dpi=160)
    bars_before = axis.bar([index - width / 2 for index in x], before, width, label="清洗前（原始缺失）", color="#9aa5b1")
    bars_after = axis.bar([index + width / 2 for index in x], after, width, label="清洗后（未知/仍缺失）", color="#2f6f9f")
    axis.set_xticks(list(x))
    axis.set_xticklabels(labels)
    axis.set_ylabel("记录数")
    axis.set_title("W2 关键字段缺失情况：清洗前 vs 清洗后")
    axis.legend()
    axis.grid(axis="y", alpha=0.25, linestyle="--")
    for bars in (bars_before, bars_after):
        for bar in bars:
            height = bar.get_height()
            axis.annotate(
                f"{int(height)}",
                xy=(bar.get_x() + bar.get_width() / 2, height),
                xytext=(0, 2),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=7,
            )
    axis.set_ylim(0, max(before + after + [1]) * 1.15)
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path)
    plt.close(figure)


def _price_values(rows: list[dict[str, str]], district: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        if row.get("city_code") != "gz" or row.get("district") != district:
            continue
        try:
            price = float(row.get("unit_price") or "")
        except ValueError:
            continue
        if price > 0:
            values.append(price)
    return values


def plot_outliers(rows: list[dict[str, str]], path: Path) -> None:
    gz = [row for row in rows if row.get("city_code") == "gz"]
    districts = sorted({row.get("district") or "" for row in gz if row.get("district")})
    all_values = [_price_values(gz, district) for district in districts]
    kept_rows = [row for row in gz if row.get("is_outlier") == "0" and row.get("model_keep") == "1"]
    kept_values = [_price_values(kept_rows, district) for district in districts]

    figure, axes = plt.subplots(1, 2, figsize=(14, 5.2), dpi=160, sharey=True)
    for axis, data, title, color in (
        (axes[0], all_values, "清洗后全部样本（含异常值）", "#c0504d"),
        (axes[1], kept_values, "剔除异常值样本（敏感性对比）", "#4f81bd"),
    ):
        axis.boxplot(data, tick_labels=districts, showfliers=False, patch_artist=True,
                     boxprops={"facecolor": color, "alpha": 0.55},
                     medianprops={"color": "#222222"})
        axis.set_title(title)
        axis.set_xticklabels(districts, rotation=45, ha="right", fontsize=8)
        axis.grid(axis="y", alpha=0.25, linestyle="--")
    axes[0].set_ylabel("单价（元/㎡）")
    figure.suptitle("广州各区县单价分布：异常值处理前后对比（隐藏离群点）")
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path)
    plt.close(figure)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 W2 预处理图表")
    parser.add_argument("--day", help="原始数据日期，如 2026-11-10；默认全部")
    parser.add_argument("--raw-root", default=str(PROJECT_ROOT / "data" / "raw"), help="原始数据根目录")
    parser.add_argument("--clean", default=str(CLEAN_PATH), help="clean.csv 路径")
    parser.add_argument("--figure-dir", default=str(FIGURE_DIR), help="图表输出目录")
    args = parser.parse_args(argv)

    configure_fonts()
    raw_counts, raw_total = read_raw_missing(Path(args.raw_root), args.day)
    rows = read_clean(Path(args.clean))
    clean_counts = clean_unknown(rows)

    figure_dir = Path(args.figure_dir)
    plot_missing(raw_counts, clean_counts, figure_dir / "missing_before_after.png")
    plot_outliers(rows, figure_dir / "outlier_boxplot.png")

    print(f"原始记录：{raw_total} 条；清洗后：{len(rows)} 条")
    print(f"图表：{figure_dir / 'missing_before_after.png'}")
    print(f"图表：{figure_dir / 'outlier_boxplot.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
