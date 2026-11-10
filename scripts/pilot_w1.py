"""W1 试点采集与字段完整率报告。

默认小样本：房天下广州 11 区 × 2 页；链家/贝壳各 3 个城市首页。
运行后写入：
- ``data/raw/...`` 原始 JSONL
- ``reports/pilot_w1_summary.json``
- ``reports/pilot_w1_fields.csv``

用法：
    python scripts/pilot_w1.py
    python scripts/pilot_w1.py --source fang --max-pages 2
    python scripts/pilot_w1.py --source lianjia --max-cities 3
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from crawler.base import CrawlStats, today_str
from crawler.beike import crawl_city_index as crawl_beike
from crawler.cities import select_cities
from crawler.fang import crawl_fang
from crawler.lianjia import crawl_city_index as crawl_lianjia
from crawler.parse import CORE_FIELDS, FIELD_ORDER

DEFAULT_RAW_ROOT = PROJECT_ROOT / "data" / "raw"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "pilot_w1_summary.json"
DEFAULT_CSV = PROJECT_ROOT / "reports" / "pilot_w1_fields.csv"

SOURCES = ("fang", "lianjia", "beike")


def is_present(value: Any) -> bool:
    return value is not None and value != "" and value != []


def read_records(paths: Iterable[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for path in paths:
        if not path.exists():
            continue
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
                records.append(record)
    return records


def source_files(source: str, raw_root: Path, day: str) -> list[Path]:
    return sorted(raw_root.glob(f"{source}/*/{day}.jsonl"))


def completeness(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    total = len(records)
    result: dict[str, dict[str, Any]] = {}
    for field in FIELD_ORDER:
        count = sum(1 for record in records if is_present(record.get(field)))
        result[field] = {
            "count": count,
            "total": total,
            "rate": round(count / total, 4) if total else 0.0,
        }
    return result


def summarize_source(source: str, records: list[dict[str, Any]], stats: CrawlStats | None) -> dict[str, Any]:
    field_stats = completeness(records)
    orientation_counter = Counter(
        record.get("orientation") or "未知" for record in records
    )
    district_counter = Counter(
        record.get("district") or "未知" for record in records
    )
    city_counter = Counter(record.get("city") or "未知" for record in records)
    mismatches = sum(1 for record in records if record.get("unit_price_mismatch") is True)
    calc_prices = sum(1 for record in records if is_present(record.get("unit_price_calc")))
    crawl_times = [record.get("crawl_time") for record in records if record.get("crawl_time")]

    notes: list[str] = []
    if source == "lianjia":
        notes.append("链家城市首页卡片不展示区县，district 置空；跨城市分析使用 city，广州主分析使用房天下数据。")
    if source in {"lianjia", "beike"}:
        notes.append("城市首页仅 31 条/页且不翻页；建成年份、装修、电梯等字段列表页可能缺失，按“未知”处理。")

    return {
        "source": source,
        "records": len(records),
        "notes": notes,
        "core_completeness": {
            field: field_stats[field] for field in CORE_FIELDS if field in field_stats
        },
        "fields": field_stats,
        "orientation_dist": dict(orientation_counter.most_common()),
        "district_dist": dict(district_counter.most_common()),
        "city_dist": dict(city_counter.most_common()),
        "unit_price_mismatch": mismatches,
        "unit_price_recomputed": calc_prices,
        "crawl_time_range": {
            "min": min(crawl_times) if crawl_times else None,
            "max": max(crawl_times) if crawl_times else None,
        },
        "stats": stats.to_dict() if stats else None,
        "sample": records[:2],
    }


def write_field_csv(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", "field", "count", "total", "rate", "rate_pct"])
        for source, payload in report["sources"].items():
            for field, item in payload["fields"].items():
                writer.writerow(
                    [
                        source,
                        field,
                        item["count"],
                        item["total"],
                        f"{item['rate']:.4f}",
                        f"{item['rate'] * 100:.1f}%",
                    ]
                )


def print_report(report: dict[str, Any]) -> None:
    print("\n=== W1 试点字段完整率 ===")
    for source, payload in report["sources"].items():
        stats = payload["stats"] or {}
        print(f"\n[{source}] 去重后 {payload['records']} 条，写入 {stats.get('written', 0)} 条")
        if stats.get("blocked"):
            print(f"  阻断：{stats.get('block_reason')} @ {stats.get('block_url')}")
        if stats.get("errors"):
            print(f"  错误：{len(stats['errors'])} 条（详见 JSON）")
        for field in CORE_FIELDS:
            item = payload["fields"].get(field)
            if item:
                print(f"  {field:<16} {item['count']:>4}/{item['total']:<4} {item['rate'] * 100:>6.1f}%")
        print(f"  朝向分布：{payload['orientation_dist']}")
        print(f"  单价校验不一致：{payload['unit_price_mismatch']} 条")
    print(f"\n合计去重后：{report['totals']['records']} 条")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="W1 试点采集 + 字段完整率报告")
    parser.add_argument("--source", choices=("all",) + SOURCES, default="all", help="采集站点，默认全部")
    parser.add_argument("--max-cities", type=int, default=3, help="链家/贝壳最多尝试城市数，默认 3")
    parser.add_argument("--max-pages", type=int, default=2, help="房天下单区县最多页数，默认 2")
    parser.add_argument("--out", default=str(DEFAULT_RAW_ROOT), help="原始数据根目录")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="汇总 JSON 路径")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help="字段完整率 CSV 路径")
    parser.add_argument("--headed", action="store_true", help="链家/贝壳显示浏览器窗口")
    parser.add_argument("--skip-crawl", action="store_true", help="只统计今天的已有数据，不发起请求")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    args = build_parser().parse_args(argv)
    raw_root = Path(args.out)
    day = today_str()
    selected = list(SOURCES) if args.source == "all" else [args.source]
    stats_by_source: dict[str, CrawlStats] = {}

    if not args.skip_crawl:
        if "fang" in selected:
            stats_by_source["fang"] = crawl_fang(
                max_pages=args.max_pages,
                out_root=raw_root,
            )
        if "lianjia" in selected:
            cities = select_cities(limit=args.max_cities)
            stats_by_source["lianjia"] = crawl_lianjia(
                cities=cities,
                out_root=raw_root,
                target_cities=args.max_cities,
                max_attempts=args.max_cities,
                headless=not args.headed,
            )
        if "beike" in selected:
            cities = select_cities(limit=args.max_cities)
            stats_by_source["beike"] = crawl_beike(
                cities=cities,
                out_root=raw_root,
                target_cities=args.max_cities,
                max_attempts=args.max_cities,
                headless=not args.headed,
            )

    report: dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "day": day,
        "params": {
            "source": args.source,
            "max_cities": args.max_cities,
            "max_pages": args.max_pages,
            "skip_crawl": args.skip_crawl,
        },
        "sources": {},
        "totals": {},
    }

    total_records = 0
    total_written = 0
    for source in selected:
        files = source_files(source, raw_root, day)
        records = read_records(files)
        payload = summarize_source(source, records, stats_by_source.get(source))
        payload["files"] = [str(path) for path in files]
        report["sources"][source] = payload
        total_records += payload["records"]
        total_written += (payload["stats"] or {}).get("written", 0)

    report["totals"] = {
        "records": total_records,
        "written": total_written,
        "target": 3000,
        "per_person_target": 1000,
        "gap_to_target": max(0, 3000 - total_records),
    }

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_field_csv(report, Path(args.csv))
    print_report(report)
    print(f"\n报告：{report_path}")
    print(f"字段表：{args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
