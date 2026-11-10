"""链家 / 贝壳多城市首页采集。

W1 实测：两站仅城市首页可稳定访问（31 条/页），``pg2``、区县页、筛选页会
跳登录或人机验证。因此这里每个城市只请求第 1 页，不翻页、不破解验证码；
任一城市出现阻断立即停止该站点并记录。
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .base import (
    BlockedError,
    BrowserFetcher,
    CrawlStats,
    FetchError,
    JsonlSink,
    setup_logging,
    today_str,
)
from .cities import City, select_cities
from .parse import parse_lianjia_like_html

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SOURCE_CONFIG = {
    "lianjia": {
        "name": "链家",
        "url_template": "https://{code}.lianjia.com/ershoufang/",
        "host_template": "{code}.lianjia.com",
    },
    "beike": {
        "name": "贝壳",
        "url_template": "https://{code}.ke.com/ershoufang/",
        "host_template": "{code}.ke.com",
    },
}


def crawl_city_index(
    source: str,
    *,
    cities: list[City] | None = None,
    out_root: str | Path = PROJECT_ROOT / "data" / "raw",
    log_dir: str | Path = PROJECT_ROOT / "logs",
    target_cities: int = 33,
    max_attempts: int = 45,
    headless: bool = True,
    min_delay: float = 2.0,
    max_delay: float = 4.0,
) -> CrawlStats:
    """按城市首页采集，成功城市数达到 ``target_cities`` 后停止。"""
    if source not in SOURCE_CONFIG:
        raise ValueError(f"未知站点：{source}")

    config = SOURCE_CONFIG[source]
    logger = setup_logging(
        f"crawler.{source}",
        log_path=Path(log_dir) / f"crawl_{source}_{today_str()}.log",
    )
    stats = CrawlStats(source=source)
    out_root = Path(out_root)
    candidates = cities if cities is not None else select_cities()
    successes = 0
    attempted = 0

    logger.info(
        "开始 %s 多城市首页采集：候选 %d 城，目标成功 %d 城，最多尝试 %d 城",
        config["name"],
        len(candidates),
        target_cities,
        max_attempts,
    )

    with BrowserFetcher(
        min_delay=min_delay,
        max_delay=max_delay,
        headless=headless,
        logger=logger,
    ) as fetcher:
        for city in candidates:
            if successes >= target_cities or attempted >= max_attempts:
                break
            attempted += 1
            url = config["url_template"].format(code=city.code)
            expected_host = config["host_template"].format(code=city.code)

            try:
                result = fetcher.fetch(url, expected_host=expected_host)
            except BlockedError as exc:
                stats.mark_block(city.name, url, exc.reason)
                logger.error("触发站点阻断（%s）：%s；按规则停止 %s 采集", exc.reason, exc.final_url or url, config["name"])
                break
            except FetchError as exc:
                stats.mark_error(city.name, str(exc))
                logger.warning("城市跳过 %s（%s）：%s", city.name, city.code, exc)
                continue

            rows = parse_lianjia_like_html(
                result.html,
                source=source,
                city=city.name,
                city_code=city.code,
                page=1,
                list_url=url,
                base_url=f"https://{expected_host}",
            )
            sink_path = out_root / source / city.code / f"{today_str()}.jsonl"
            with JsonlSink(sink_path) as sink:
                written = sink.write_many(rows)
                duplicate = sink.duplicates
            stats.add_page(city.name, rows=len(rows), written=written, duplicate=duplicate, list_url=url)

            if rows:
                successes += 1
                logger.info(
                    "%s %s：解析 %d 条，新写入 %d 条，重复 %d 条（累计 %d/%d 城）",
                    config["name"],
                    city.name,
                    len(rows),
                    written,
                    duplicate,
                    successes,
                    target_cities,
                )
            else:
                logger.warning("%s %s：页面可访问但未解析到房源（%s）", config["name"], city.name, result.final_url)

    stats.finish()
    logger.info(
        "%s 采集结束：成功 %d 城，请求 %d 页，解析 %d 条，写入 %d 条，阻断=%s",
        config["name"],
        successes,
        stats.pages,
        stats.rows,
        stats.written,
        stats.block_reason or "无",
    )
    return stats


def build_parser(source: str) -> argparse.ArgumentParser:
    config = SOURCE_CONFIG[source]
    parser = argparse.ArgumentParser(description=f"{config['name']}多城市首页采集（每城 1 页）")
    parser.add_argument("--cities", help="逗号分隔的城市代码，如 gz,sz,bj；默认按候选顺序")
    parser.add_argument("--target-cities", type=int, default=33, help="目标成功城市数，默认 33")
    parser.add_argument("--max-attempts", type=int, default=45, help="最多尝试城市数，默认 45")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "data" / "raw"), help="原始数据根目录")
    parser.add_argument("--log-dir", default=str(PROJECT_ROOT / "logs"), help="日志目录")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口（默认无头）")
    parser.add_argument("--min-delay", type=float, default=2.0, help="请求最小间隔秒数")
    parser.add_argument("--max-delay", type=float, default=4.0, help="请求最大间隔秒数")
    parser.add_argument("--json", help="可选：把本次统计写入 JSON 文件")
    return parser


def main_for(source: str, argv: list[str] | None = None) -> int:
    parser = build_parser(source)
    args = parser.parse_args(argv)
    city_codes = [code.strip() for code in args.cities.split(",")] if args.cities else None
    cities = select_cities(city_codes)
    if not cities:
        parser.error("没有匹配的城市代码")

    stats = crawl_city_index(
        source,
        cities=cities,
        out_root=args.out,
        log_dir=args.log_dir,
        target_cities=args.target_cities,
        max_attempts=args.max_attempts,
        headless=not args.headed,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
    )
    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stats.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if stats.blocked and stats.written == 0 else 0


def main(source: str) -> None:
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main_for(source))
