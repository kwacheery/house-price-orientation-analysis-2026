"""从当天原始 JSONL 生成脱敏样本，供仓库提交与课堂演示。

默认每个站点 60 条，按城市（链家/贝壳）或区县（房天下）分层抽样；
输出 ``data/samples/{source}_sample.csv``，原始数据仍保留在 ``data/raw/`` 不入库。

用法：
    python scripts/make_samples.py
    python scripts/make_samples.py --per-source 80 --day 2026-11-10
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from crawler.base import today_str
from crawler.parse import FIELD_ORDER

SOURCES = ("fang", "lianjia", "beike")
SAMPLE_FIELDS = [field for field in FIELD_ORDER if field != "list_url"]


def read_source(raw_root: Path, source: str, day: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(raw_root.glob(f"{source}/*/{day}.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                url = str(record.get("url") or "")
                if url and url in seen:
                    continue
                if url:
                    seen.add(url)
                records.append(record)
    return records


def stratified_sample(
    records: Iterable[dict[str, Any]],
    *,
    key: str,
    size: int,
    seed: int = 42,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[str(record.get(key) or "未知")].append(record)

    rng = random.Random(seed)
    for group in groups.values():
        rng.shuffle(group)

    sampled: list[dict[str, Any]] = []
    keys = sorted(groups)
    while len(sampled) < size:
        progressed = False
        for group_key in keys:
            if groups[group_key]:
                sampled.append(groups[group_key].pop())
                progressed = True
                if len(sampled) >= size:
                    break
        if not progressed:
            break
    return sampled


def write_csv(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SAMPLE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field) for field in SAMPLE_FIELDS})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成各站点脱敏样本 CSV")
    parser.add_argument("--day", default=today_str(), help="原始数据日期，默认今天")
    parser.add_argument("--raw-root", default=str(PROJECT_ROOT / "data" / "raw"), help="原始数据根目录")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "data" / "samples"), help="样本输出目录")
    parser.add_argument("--per-source", type=int, default=60, help="每站样本条数，默认 60")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    args = parser.parse_args(argv)

    raw_root = Path(args.raw_root)
    out_root = Path(args.out)
    for source in SOURCES:
        records = read_source(raw_root, source, args.day)
        if not records:
            print(f"[{source}] 没有 {args.day} 的原始数据，跳过")
            continue
        key = "district" if source == "fang" else "city"
        sample = stratified_sample(records, key=key, size=args.per_source, seed=args.seed)
        path = out_root / f"{source}_sample.csv"
        write_csv(sample, path)
        print(f"[{source}] 原始 {len(records)} 条，抽样 {len(sample)} 条 -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
