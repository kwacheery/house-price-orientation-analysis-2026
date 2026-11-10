"""房天下广州二手房采集（11 区县独立翻页）。

分页规则（W1 实测）：
- 第 1 页：``https://gz.esf.fang.com/house-a073/``
- 第 n 页（n>=2）：``https://gz.esf.fang.com/house-a073/i{30+n}/``
- 每页 60 条，区县路径互不影响。

用法：
    python -m crawler.fang --max-pages 2
    python -m crawler.fang --districts 天河区,越秀区 --max-pages 3
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .base import (
    BlockedError,
    CrawlStats,
    FetchError,
    HttpFetcher,
    JsonlSink,
    setup_logging,
    today_str,
)
from .parse import parse_fang_html

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://gz.esf.fang.com"
EXPECTED_HOST = "gz.esf.fang.com"

# (区县全称, 房天下路径代码)
GZ_DISTRICTS: list[tuple[str, str]] = [
    ("天河区", "a073"),
    ("越秀区", "a072"),
    ("荔湾区", "a071"),
    ("海珠区", "a074"),
    ("白云区", "a076"),
    ("番禺区", "a078"),
    ("黄埔区", "a075"),
    ("花都区", "a0639"),
    ("南沙区", "a084"),
    ("从化区", "a079"),
    ("增城区", "a080"),
]


def district_url(code: str, page: int) -> str:
    if page <= 1:
        return f"{BASE_URL}/house-{code}/"
    return f"{BASE_URL}/house-{code}/i{30 + page}/"


def crawl_fang(
    *,
    districts: list[tuple[str, str]] | None = None,
    max_pages: int = 5,
    out_root: str | Path = PROJECT_ROOT / "data" / "raw",
    log_dir: str | Path = PROJECT_ROOT / "logs",
    min_delay: float = 1.5,
    max_delay: float = 3.0,
) -> CrawlStats:
    """按区县顺序翻页；单区县空页即结束，出现阻断则停止整个站点。"""
    logger = setup_logging(
        "crawler.fang",
        log_path=Path(log_dir) / f"crawl_fang_{today_str()}.log",
    )
    stats = CrawlStats(source="fang")
    out_root = Path(out_root)
    targets = districts or GZ_DISTRICTS
    sink_path = out_root / "fang" / "gz" / f"{today_str()}.jsonl"

    logger.info("开始房天下广州采集：%d 个区县，单区县最多 %d 页", len(targets), max_pages)

    with HttpFetcher(min_delay=min_delay, max_delay=max_delay, logger=logger) as fetcher:
        with JsonlSink(sink_path) as sink:
            for district_name, code in targets:
                for page in range(1, max_pages + 1):
                    url = district_url(code, page)
                    try:
                        result = fetcher.fetch(url, expected_host=EXPECTED_HOST)
                    except BlockedError as exc:
                        stats.mark_block(district_name, url, exc.reason)
                        logger.error(
                            "触发站点阻断（%s）：%s；按规则停止房天下采集",
                            exc.reason,
                            exc.final_url or url,
                        )
                        stats.finish()
                        return stats
                    except FetchError as exc:
                        stats.mark_error(district_name, f"{url} -> {exc}")
                        logger.warning("区县跳过 %s 第 %d 页：%s", district_name, page, exc)
                        break

                    rows = parse_fang_html(
                        result.html,
                        district=district_name,
                        page=page,
                        list_url=url,
                    )
                    written = sink.write_many(rows)
                    stats.add_page(
                        district_name,
                        rows=len(rows),
                        written=written,
                        duplicate=sink.duplicates,
                        list_url=url,
                    )
                    logger.info(
                        "房天下 %s 第 %d 页：解析 %d 条，新写入 %d 条（累计 %d 条）",
                        district_name,
                        page,
                        len(rows),
                        written,
                        stats.written,
                    )
                    if not rows:
                        logger.info("房天下 %s 第 %d 页无房源，结束该区县", district_name, page)
                        break

    stats.finish()
    logger.info(
        "房天下采集结束：%d 页，解析 %d 条，写入 %d 条，阻断=%s",
        stats.pages,
        stats.rows,
        stats.written,
        stats.block_reason or "无",
    )
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="房天下广州二手房采集（区县翻页）")
    parser.add_argument(
        "--districts",
        help="逗号分隔的区县名称或代码，如 天河区,越秀区 或 a073,a072；默认 11 区",
    )
    parser.add_argument("--max-pages", type=int, default=5, help="单区县最多页数，默认 5")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "data" / "raw"), help="原始数据根目录")
    parser.add_argument("--log-dir", default=str(PROJECT_ROOT / "logs"), help="日志目录")
    parser.add_argument("--min-delay", type=float, default=1.5, help="请求最小间隔秒数")
    parser.add_argument("--max-delay", type=float, default=3.0, help="请求最大间隔秒数")
    parser.add_argument("--json", help="可选：把本次统计写入 JSON 文件")
    return parser


def select_districts(value: str | None) -> list[tuple[str, str]]:
    if not value:
        return list(GZ_DISTRICTS)
    by_name = {name: code for name, code in GZ_DISTRICTS}
    by_code = {code: (name, code) for name, code in GZ_DISTRICTS}
    selected: list[tuple[str, str]] = []
    for item in value.split(","):
        key = item.strip()
        if not key:
            continue
        if key in by_name:
            selected.append((key, by_name[key]))
        elif key in by_code:
            selected.append(by_code[key])
    return selected


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    parser = build_parser()
    args = parser.parse_args(argv)
    districts = select_districts(args.districts)
    if not districts:
        parser.error("没有匹配的区县名称或代码")

    stats = crawl_fang(
        districts=districts,
        max_pages=args.max_pages,
        out_root=args.out,
        log_dir=args.log_dir,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
    )
    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stats.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if stats.blocked and stats.written == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
