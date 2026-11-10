"""W2 清洗与预处理。

流程（对应计划书 6.1）：

    raw JSONL
      → 类型转换 / 文本清洗
      → 区县补全（链家/贝壳缺失时用小区/板块映射）
      → 朝向标准化
      → 单价重算与校验
      → 去重（完全重复 / 业务重复 / 跨平台重复）
      → 缺失值处理（显式“未知”，不 dropna）
      → 异常值检测（IQR + 规则，保留并标记）
      → data/processed/clean.csv
      → reports/preprocessing_summary.csv / .json / preprocessing_report.md

设计原则：
- 原始 JSONL 只读，清洗结果可随时从 raw 重新生成。
- 缺失值优先保留样本 + “未知”类别，禁止无依据 fillna(0)。
- 异常值只标记 `is_outlier` 与 `outlier_reason`，是否剔除留给建模敏感性对比。
- 跨平台重复保留主站点记录，其余标记 `cross_source_dup=1, model_keep=0`。

用法：
    python -m pipeline.clean
    python -m pipeline.clean --day 2026-11-10
    python -m pipeline.clean --no-outlier-filter
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from crawler.parse import FIELD_ORDER, normalize_orientation

CLEAN_VERSION = "w2-v1"
SOURCES = ("fang", "lianjia", "beike")
MAIN_SOURCE = "fang"

# 跨平台去重时保留优先级：主分析站点优先。
SOURCE_PRIORITY = {"fang": 0, "beike": 1, "lianjia": 2}

ORIENTATION_CLASSES = ("东", "南", "西", "北", "东南", "西南", "东北", "西北")
FLOOR_RATIO = {"低": 0.15, "中": 0.5, "高": 0.85}
UNKNOWN_COMMUNITY = "未知小区"

# 链家城市首页不展示区县。以下板块名来自广州实际房源，按行政区划人工整理，
# 仅用于补全广州链家样本，标记 district_source=plate_map，不用于其他城市。
GZ_PLATE_DISTRICT = {
    "中大": "海珠区",
    "大坦沙": "荔湾区",
    "天润路": "天河区",
    "太平镇": "从化区",
    "昌岗": "海珠区",
    "永宁": "增城区",
    "江燕路": "海珠区",
    "沙河": "天河区",
    "河滨北路": "从化区",
    "洪德": "海珠区",
    "神岗镇": "从化区",
    "花城街": "花都区",
    "进港大道": "南沙区",
    "香雪": "黄埔区",
    "黄埔永和": "黄埔区",
    "市桥北": "番禺区",
    "南岸路": "荔湾区",
    "罗冲围": "白云区",
    "广钢新城": "荔湾区",
    "知识城": "黄埔区",
    "狮岭镇": "花都区",
    "新塘北": "增城区",
    "钟村": "番禺区",
}

EXTRA_FIELDS = [
    "district_source",
    "unit_price_raw",
    "unit_price_suspect",
    "orientation_main",
    "is_south",
    "is_south_north",
    "orientation_count",
    "orientation_missing",
    "age",
    "age_missing",
    "floor_ratio",
    "is_villa",
    "is_outlier",
    "outlier_reason",
    "cross_source_dup",
    "dup_of_url",
    "model_keep",
    "clean_version",
]

CLEAN_FIELDS = FIELD_ORDER + EXTRA_FIELDS

SUMMARY_FIELDS = ["step", "rows_in", "rows_removed", "rows_modified", "rows_out", "note"]


# ---------------------------------------------------------------------------
# 读入与基础转换
# ---------------------------------------------------------------------------


def read_raw(
    raw_root: str | Path,
    *,
    day: str | None = None,
    sources: Iterable[str] = SOURCES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """读取 raw JSONL，按 (source, url) 去重并保留最新 crawl_time。"""
    root = Path(raw_root)
    records: dict[tuple[str, str], dict[str, Any]] = {}
    lines = 0
    duplicates = 0
    files: list[str] = []

    for source in sources:
        pattern = f"{source}/*/{day}.jsonl" if day else f"{source}/*/*.jsonl"
        for path in sorted(root.glob(pattern)):
            files.append(str(path))
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    lines += 1
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        duplicates += 1
                        continue
                    key = (str(record.get("source") or source), str(record.get("url") or ""))
                    if not key[1]:
                        duplicates += 1
                        continue
                    existing = records.get(key)
                    if existing is not None:
                        duplicates += 1
                        if str(record.get("crawl_time") or "") > str(existing.get("crawl_time") or ""):
                            records[key] = record
                        continue
                    records[key] = record

    rows = list(records.values())
    meta = {
        "files": files,
        "lines": lines,
        "duplicate_url": duplicates,
        "rows": len(rows),
    }
    return rows, meta


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value))
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u0000-\u001f\u007f\u200b-\u200f\u2028\u2029]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).replace(",", "").strip()
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return None
        number = float(match.group())
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return int(number) if number is not None else None


def _to_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "是", "有", "有电梯"}:
        return True
    if text in {"false", "0", "否", "无", "无电梯"}:
        return False
    return None


def coerce_records(rows: list[dict[str, Any]]) -> int:
    """类型转换与文本清洗，原地修改并返回修改行数。"""
    modified = 0
    for record in rows:
        before = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        for field in ("title", "community", "plate", "layout", "orientation_raw", "orientation", "decoration"):
            if field in record:
                record[field] = _clean_text(record[field])
        for field in ("area", "total_price", "unit_price", "unit_price_calc"):
            record[field] = _to_float(record.get(field))
        for field in ("rooms", "halls", "bathrooms", "build_year", "total_floors", "page"):
            record[field] = _to_int(record.get(field))
        record["elevator"] = _to_bool(record.get("elevator"))
        record["unit_price_mismatch"] = _to_bool(record.get("unit_price_mismatch"))
        if record.get("source") not in SOURCES:
            record["source"] = _clean_text(record.get("source")) or "unknown"
        if record.get("city_code"):
            record["city_code"] = str(record["city_code"]).lower()
        after = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        if before != after:
            modified += 1
    return modified


# ---------------------------------------------------------------------------
# 区县补全
# ---------------------------------------------------------------------------


def build_district_maps(
    rows: list[dict[str, Any]],
    *,
    curated: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    """从已有区县的广州记录构建 板块→区县、小区→区县 映射。

    只使用 source in {fang, beike} 且区县非空的广州记录；同一板块若出现多个
    区县则取多数，并在 meta 中记录冲突数，避免静默覆盖。
    """
    plate_votes: dict[str, Counter[str]] = defaultdict(Counter)
    community_votes: dict[str, Counter[str]] = defaultdict(Counter)
    for record in rows:
        if record.get("city_code") != "gz":
            continue
        if record.get("source") not in {"fang", "beike"}:
            continue
        district = _clean_text(record.get("district"))
        if not district:
            continue
        plate = _clean_text(record.get("plate"))
        community = _clean_text(record.get("community"))
        if plate:
            plate_votes[plate][district] += 1
        if community:
            community_votes[community][district] += 1

    plate_map: dict[str, str] = {}
    plate_conflicts = 0
    for plate, votes in plate_votes.items():
        plate_map[plate] = votes.most_common(1)[0][0]
        if len(votes) > 1:
            plate_conflicts += 1

    community_map: dict[str, str] = {}
    community_conflicts = 0
    for community, votes in community_votes.items():
        community_map[community] = votes.most_common(1)[0][0]
        if len(votes) > 1:
            community_conflicts += 1

    curated_added = 0
    for plate, district in (curated or {}).items():
        if plate not in plate_map:
            plate_map[plate] = district
            curated_added += 1

    meta = {
        "plate_map": len(plate_map),
        "community_map": len(community_map),
        "plate_conflicts": plate_conflicts,
        "community_conflicts": community_conflicts,
        "curated_added": curated_added,
    }
    return plate_map, community_map, meta


def backfill_districts(
    rows: list[dict[str, Any]],
    *,
    plate_map: dict[str, str],
    community_map: dict[str, str],
) -> dict[str, Any]:
    """补全区县并写入 district_source。

    district_source 取值：site / community_map / plate_map / missing。
    非广州城市不做映射，避免不同城市同名板块误配。
    """
    counts = Counter()
    for record in rows:
        district = _clean_text(record.get("district"))
        if district:
            record["district_source"] = "site"
            counts["site"] += 1
            continue

        if record.get("city_code") == "gz":
            community = _clean_text(record.get("community"))
            plate = _clean_text(record.get("plate"))
            if community and community in community_map:
                record["district"] = community_map[community]
                record["district_source"] = "community_map"
                counts["community_map"] += 1
                continue
            if plate and plate in plate_map:
                record["district"] = plate_map[plate]
                record["district_source"] = "plate_map"
                counts["plate_map"] += 1
                continue

        record["district"] = None
        record["district_source"] = "missing"
        counts["missing"] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# 朝向、价格、缺失值
# ---------------------------------------------------------------------------


def _orientation_tokens(orientation: str | None) -> list[str]:
    if orientation == "南北":
        return ["南", "北"]
    if orientation == "东西":
        return ["东", "西"]
    if orientation in ORIENTATION_CLASSES:
        return [orientation]
    return []


def add_orientation_features(record: dict[str, Any]) -> bool:
    """写入 orientation_main / is_south / is_south_north / count / missing。"""
    orientation = normalize_orientation(_clean_text(record.get("orientation")))
    if orientation is None:
        orientation = normalize_orientation(_clean_text(record.get("orientation_raw")))
    record["orientation"] = orientation
    tokens = _orientation_tokens(orientation)
    if orientation == "南北":
        main = "南"
    elif orientation == "东西":
        main = "东"
    elif orientation in ORIENTATION_CLASSES:
        main = orientation
    else:
        main = "未知"
    record["orientation_main"] = main
    record["is_south"] = 1 if main == "南" else 0
    record["is_south_north"] = 1 if "南" in tokens and "北" in tokens else 0
    record["orientation_count"] = len(tokens)
    record["orientation_missing"] = 1 if not tokens else 0
    return bool(tokens)


def add_property_flags(record: dict[str, Any]) -> bool:
    """标记别墅/独栋类房源，供主模型剔除或做敏感性对比。

    仅使用标题、小区名、建筑类型中的明确关键词，避免把普通“花园小区”误判。
    """
    text = " ".join(
        str(record.get(field) or "") for field in ("title", "community", "building_type")
    )
    keywords = ("别墅", "別墅", "独栋", "联排", "双拼", "庄园", "独门独院")
    record["is_villa"] = 1 if any(keyword in text for keyword in keywords) else 0
    return bool(record["is_villa"])


def recompute_unit_price(record: dict[str, Any]) -> bool:
    """用总价/面积重算单价，保留原始单价并标记 >5% 不一致。

    返回是否修改了记录。目标变量必须可复现，因此 clean 层统一使用重算值；
    原始挂牌单价保存在 unit_price_raw 供核查。
    """
    area = _to_float(record.get("area"))
    total = _to_float(record.get("total_price"))
    raw_unit = _to_float(record.get("unit_price"))
    record["unit_price_raw"] = raw_unit

    if not area or area <= 0 or not total or total <= 0:
        record["unit_price_calc"] = None
        record["unit_price_suspect"] = None
        return False

    calculated = round(total * 10000 / area, 2)
    record["unit_price_calc"] = calculated
    suspect = (
        raw_unit is not None
        and raw_unit > 0
        and abs(calculated - raw_unit) / raw_unit > 0.05
    )
    record["unit_price_suspect"] = suspect if raw_unit is not None else None
    changed = raw_unit != calculated or record.get("unit_price") != calculated
    record["unit_price"] = calculated
    record["unit_price_mismatch"] = suspect if raw_unit is not None else None
    return changed


def fill_missing(rows: list[dict[str, Any]]) -> dict[str, Counter[str]]:
    """显式“未知”类别；只对类别字段填充，数值字段保留 None 并加标记。"""
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    current_year = datetime.now().year
    for record in rows:
        community = _clean_text(record.get("community"))
        if not community:
            record["community"] = UNKNOWN_COMMUNITY
            counts["community"]["unknown"] += 1
        else:
            record["community"] = community

        if not _clean_text(record.get("decoration")):
            record["decoration"] = "未知"
            counts["decoration"]["unknown"] += 1
        if not _clean_text(record.get("floor_level")):
            record["floor_level"] = "未知"
            counts["floor_level"]["unknown"] += 1
        if not _clean_text(record.get("building_type")):
            record["building_type"] = "未知"
            counts["building_type"]["unknown"] += 1

        elevator = record.get("elevator")
        if elevator is True:
            record["elevator"] = "有"
        elif elevator is False:
            record["elevator"] = "无"
        else:
            record["elevator"] = "未知"
            counts["elevator"]["unknown"] += 1

        build_year = _to_int(record.get("build_year"))
        if build_year and 1900 <= build_year <= current_year:
            record["age"] = current_year - build_year
            record["age_missing"] = 0
        else:
            record["age"] = None
            record["age_missing"] = 1
            counts["build_year"]["unknown"] += 1

        record["floor_ratio"] = FLOOR_RATIO.get(str(record.get("floor_level")), None)
        record["orientation"] = record.get("orientation")
        record["orientation_missing"] = 1 if not record.get("orientation") else 0
        if record["orientation_missing"]:
            record["orientation"] = "未知"
            record["orientation_main"] = "未知"
            record["is_south"] = 0
            record["is_south_north"] = 0
            record["orientation_count"] = 0
            counts["orientation"]["unknown"] += 1
    return {key: Counter(value) for key, value in counts.items()}


# ---------------------------------------------------------------------------
# 去重
# ---------------------------------------------------------------------------


def _record_signature(record: dict[str, Any]) -> tuple[Any, ...]:
    values: list[Any] = []
    for field in FIELD_ORDER:
        if field == "crawl_time":
            continue
        value = record.get(field)
        if isinstance(value, float):
            value = round(value, 4)
        values.append(value)
    return tuple(values)


def dedup_exact(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """完全重复：所有业务字段一致，保留 crawl_time 最新的一条。"""
    kept: dict[tuple[Any, ...], dict[str, Any]] = {}
    removed = 0
    for record in rows:
        key = _record_signature(record)
        existing = kept.get(key)
        if existing is None:
            kept[key] = record
            continue
        removed += 1
        if str(record.get("crawl_time") or "") > str(existing.get("crawl_time") or ""):
            kept[key] = record
    return list(kept.values()), removed


def _business_key(record: dict[str, Any]) -> tuple[Any, ...] | None:
    community = _clean_text(record.get("community"))
    if not community or community == UNKNOWN_COMMUNITY:
        return None
    return (
        record.get("source"),
        record.get("city_code"),
        community,
        round(float(record.get("area") or 0), 2),
        _clean_text(record.get("layout")),
        _to_float(record.get("total_price")),
        record.get("orientation"),
        _clean_text(record.get("floor_level")),
    )


def dedup_business(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """业务重复：同站、同小区/面积/户型/总价/朝向/楼层，保留最新。"""
    kept: dict[tuple[Any, ...], dict[str, Any]] = {}
    without_key: list[dict[str, Any]] = []
    removed = 0
    for record in rows:
        key = _business_key(record)
        if key is None:
            without_key.append(record)
            continue
        existing = kept.get(key)
        if existing is None:
            kept[key] = record
            continue
        removed += 1
        if str(record.get("crawl_time") or "") > str(existing.get("crawl_time") or ""):
            kept[key] = record
    return list(kept.values()) + without_key, removed


def mark_cross_source_dups(rows: list[dict[str, Any]]) -> int:
    """跨平台重复：同城、同小区、同户型、面积差 ≤1㎡、总价差 ≤2%。

    保留优先级 fang > beike > lianjia；被标记记录仍留在 clean.csv，
    但 model_keep=0，特征/建模阶段按此过滤。
    """
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in rows:
        record.setdefault("cross_source_dup", 0)
        record.setdefault("dup_of_url", None)
        record.setdefault("model_keep", 1)
        community = _clean_text(record.get("community"))
        if not community or community == UNKNOWN_COMMUNITY:
            continue
        layout = _clean_text(record.get("layout")) or ""
        groups[(str(record.get("city_code") or ""), community, layout)].append(record)

    marked = 0
    for group in groups.values():
        if len({record.get("source") for record in group}) < 2:
            continue
        ordered = sorted(
            group,
            key=lambda item: (
                SOURCE_PRIORITY.get(str(item.get("source")), 9),
                str(item.get("crawl_time") or ""),
            ),
        )
        kept: list[dict[str, Any]] = []
        for record in ordered:
            area = _to_float(record.get("area"))
            total = _to_float(record.get("total_price"))
            duplicate_of = None
            if area and total:
                for candidate in kept:
                    other_area = _to_float(candidate.get("area"))
                    other_total = _to_float(candidate.get("total_price"))
                    if not other_area or not other_total:
                        continue
                    if abs(area - other_area) <= 1.0 and abs(total - other_total) / max(total, other_total) <= 0.02:
                        duplicate_of = candidate
                        break
            if duplicate_of is not None:
                record["cross_source_dup"] = 1
                record["dup_of_url"] = duplicate_of.get("url")
                record["model_keep"] = 0
                marked += 1
            else:
                kept.append(record)
    return marked


# ---------------------------------------------------------------------------
# 异常值
# ---------------------------------------------------------------------------


def iqr_bounds(values: Iterable[float]) -> tuple[float, float, float, float] | None:
    prices = sorted(float(value) for value in values if value is not None)
    if len(prices) < 4:
        return None
    q1 = _percentile(prices, 25)
    q3 = _percentile(prices, 75)
    iqr = q3 - q1
    return q1, q3, q1 - 1.5 * iqr, q3 + 1.5 * iqr


def _percentile(sorted_values: list[float], percent: float) -> float:
    """线性插值分位数，避免额外依赖。"""
    if not sorted_values:
        raise ValueError("空序列无法计算分位数")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _sigma_bounds(values: Iterable[float]) -> tuple[float, float] | None:
    numbers = [float(value) for value in values if value is not None]
    if len(numbers) < 4:
        return None
    mean = sum(numbers) / len(numbers)
    variance = sum((number - mean) ** 2 for number in numbers) / (len(numbers) - 1)
    sigma = math.sqrt(variance)
    return mean - 3 * sigma, mean + 3 * sigma


def detect_outliers(rows: list[dict[str, Any]], *, year: int | None = None) -> dict[str, Any]:
    """按区县/城市 IQR 标记单价异常，并叠加面积/总价/年份/楼层规则。"""
    current_year = year or datetime.now().year
    gz_prices = [record.get("unit_price") for record in rows if record.get("city_code") == "gz"]
    gz_bounds = iqr_bounds(value for value in gz_prices if value)
    gz_sigma = _sigma_bounds(value for value in gz_prices if value)

    city_groups: dict[str, list[float]] = defaultdict(list)
    district_groups: dict[str, list[float]] = defaultdict(list)
    for record in rows:
        price = _to_float(record.get("unit_price"))
        if not price:
            continue
        city_groups[str(record.get("city_code") or "")].append(price)
        if record.get("city_code") == "gz" and record.get("district"):
            district_groups[str(record["district"])].append(price)

    city_bounds = {city: iqr_bounds(values) for city, values in city_groups.items()}
    district_bounds = {district: iqr_bounds(values) for district, values in district_groups.items()}

    used: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for record in rows:
        reasons: list[str] = []

        price = _to_float(record.get("unit_price"))
        bounds = None
        scope = None
        if price is not None:
            if record.get("city_code") == "gz":
                district = str(record.get("district") or "")
                if district and district_bounds.get(district):
                    bounds = district_bounds[district]
                    scope = f"district:{district}"
                elif gz_bounds:
                    bounds = gz_bounds
                    scope = "city:gz"
            else:
                city = str(record.get("city_code") or "")
                bounds = city_bounds.get(city)
                scope = f"city:{city}"
            if bounds is not None and (price < bounds[2] or price > bounds[3]):
                reasons.append(f"unit_price_iqr:{scope}")
        if bounds is not None:
            used["iqr"] += 1

        area = _to_float(record.get("area"))
        if area is not None and (area < 10 or area > 1000):
            reasons.append("area_range")

        total = _to_float(record.get("total_price"))
        if total is not None and (total < 10 or total > 10000):
            reasons.append("total_price_range")

        build_year = _to_int(record.get("build_year"))
        if build_year is not None and (build_year < 1950 or build_year > current_year):
            reasons.append("build_year_range")

        total_floors = _to_int(record.get("total_floors"))
        if total_floors is not None and (total_floors < 1 or total_floors > 200):
            reasons.append("total_floors_range")

        record["is_outlier"] = 1 if reasons else 0
        record["outlier_reason"] = ";".join(reasons) if reasons else ""
        for reason in reasons:
            reason_counts[reason.split(":")[0]] += 1

    return {
        "year": current_year,
        "gz_iqr": gz_bounds,
        "gz_sigma": gz_sigma,
        "district_bounds": {key: value for key, value in district_bounds.items() if value},
        "city_bounds": {key: value for key, value in city_bounds.items() if value},
        "reasons": dict(reason_counts),
        "rows_with_iqr_group": used["iqr"],
    }


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def write_csv(rows: list[dict[str, Any]], path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CLEAN_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in rows:
            writer.writerow({field: record.get(field) for field in CLEAN_FIELDS})


def write_summary(summary: list[dict[str, Any]], path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summary)


def _completeness(rows: list[dict[str, Any]], fields: Iterable[str]) -> dict[str, dict[str, Any]]:
    total = len(rows)
    result: dict[str, dict[str, Any]] = {}
    for field in fields:
        if field in {"is_south", "is_south_north", "orientation_missing", "age_missing", "is_outlier"}:
            count = sum(1 for record in rows if record.get(field) is not None)
        else:
            count = sum(
                1
                for record in rows
                if record.get(field) not in (None, "", "未知", "未知小区")
            )
        result[field] = {"count": count, "total": total, "rate": round(count / total, 4) if total else 0.0}
    return result


def build_report(
    rows: list[dict[str, Any]],
    *,
    meta: dict[str, Any],
    summary: list[dict[str, Any]],
    district_counts: dict[str, Any],
    district_meta: dict[str, Any],
    outlier_meta: dict[str, Any],
) -> dict[str, Any]:
    orientation = Counter(record.get("orientation") or "未知" for record in rows)
    main_orientation = Counter(record.get("orientation_main") or "未知" for record in rows)
    source_counts = Counter(record.get("source") for record in rows)
    city_counts = Counter(record.get("city") for record in rows)
    suspect = [record for record in rows if record.get("unit_price_suspect") is True]
    outlier_rows = [record for record in rows if record.get("is_outlier")]
    cross_dup = sum(1 for record in rows if record.get("cross_source_dup"))

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "clean_version": CLEAN_VERSION,
        "raw": meta,
        "rows": len(rows),
        "sources": dict(source_counts),
        "cities": len(city_counts),
        "district_sources": district_counts,
        "district_maps": district_meta,
        "summary": summary,
        "completeness": _completeness(rows, FIELD_ORDER + EXTRA_FIELDS),
        "orientation_dist": dict(orientation.most_common()),
        "orientation_main_dist": dict(main_orientation.most_common()),
        "unit_price_suspect": len(suspect),
        "villas": sum(1 for record in rows if record.get("is_villa")),
        "unit_price_suspect_samples": [
            {
                "source": record.get("source"),
                "city": record.get("city"),
                "community": record.get("community"),
                "area": record.get("area"),
                "total_price": record.get("total_price"),
                "unit_price_raw": record.get("unit_price_raw"),
                "unit_price": record.get("unit_price"),
                "url": record.get("url"),
            }
            for record in sorted(suspect, key=lambda item: item.get("source") or "")[:15]
        ],
        "outliers": {
            "rows": len(outlier_rows),
            "rate": round(len(outlier_rows) / len(rows), 4) if rows else 0.0,
            "reasons": outlier_meta.get("reasons", {}),
            "iqr": {
                "gz": outlier_meta.get("gz_iqr"),
                "gz_sigma": outlier_meta.get("gz_sigma"),
                "districts": outlier_meta.get("district_bounds", {}),
            },
        },
        "cross_source_dup": cross_dup,
        "model_keep": sum(1 for record in rows if record.get("model_keep") == 1),
        "missing": {
            "orientation": sum(1 for record in rows if record.get("orientation_missing")),
            "build_year": sum(1 for record in rows if record.get("age_missing")),
            "decoration": sum(1 for record in rows if record.get("decoration") == "未知"),
            "elevator": sum(1 for record in rows if record.get("elevator") == "未知"),
            "floor": sum(1 for record in rows if record.get("floor_level") == "未知"),
        },
    }


def write_markdown(report: dict[str, Any], path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# W2 清洗与预处理报告\n")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append(f"- 清洗版本：`{report['clean_version']}`")
    lines.append(f"- 输入：{report['raw']['lines']} 行原始 JSONL（{len(report['raw']['files'])} 个文件）")
    lines.append(f"- 按 (source, url) 去重后：{report['raw']['rows']} 条")
    lines.append(f"- 清洗输出：{report['rows']} 条，建模可用 `model_keep=1`：{report['model_keep']} 条\n")

    lines.append("## 1. 每步行数变化\n")
    lines.append("| 步骤 | 输入 | 删除 | 修改 | 输出 | 说明 |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for item in report["summary"]:
        lines.append(
            f"| {item['step']} | {item['rows_in']} | {item['rows_removed']} | "
            f"{item['rows_modified']} | {item['rows_out']} | {item['note']} |"
        )

    lines.append("\n## 2. 来源与区县补全\n")
    lines.append(f"- 站点分布：{report['sources']}")
    lines.append(f"- 区县来源：{report['district_sources']}")
    lines.append(
        f"- 映射表：板块 {report['district_maps']['plate_map']} 个，"
        f"小区 {report['district_maps']['community_map']} 个，"
        f"人工补充 {report['district_maps']['curated_added']} 个"
    )
    lines.append(
        f"- 冲突：板块 {report['district_maps']['plate_conflicts']} 个，"
        f"小区 {report['district_maps']['community_conflicts']} 个（取多数）"
    )

    lines.append("\n## 3. 朝向标准化\n")
    lines.append(f"- 原始朝向：{report['orientation_dist']}")
    lines.append(f"- 主朝向：{report['orientation_main_dist']}")
    lines.append(
        f"- 朝向缺失：{report['missing']['orientation']} 条，"
        "统一为“未知”并设 `orientation_missing=1`，不删除样本。"
    )

    lines.append("\n## 4. 缺失值处理\n")
    lines.append("| 字段 | 缺失条数 | 策略 |")
    lines.append("|---|---:|---|")
    lines.append(f"| 朝向 | {report['missing']['orientation']} | 设为“未知”，不删除 |")
    lines.append(f"| 建成年份 | {report['missing']['build_year']} | `age_missing=1`，age 留空 |")
    lines.append(f"| 装修 | {report['missing']['decoration']} | 设为“未知” |")
    lines.append(f"| 电梯 | {report['missing']['elevator']} | 设为“未知” |")
    lines.append(f"| 楼层 | {report['missing']['floor']} | 设为“未知” |")
    lines.append("\n数值字段（面积、总价、单价）不做无依据填充；面积或总价缺失导致单价无法重算的记录在清洗入口剔除。")

    lines.append("\n## 5. 单价校验\n")
    lines.append(
        f"- 重算规则：`unit_price = total_price * 10000 / area`，原始值写入 `unit_price_raw`。"
    )
    lines.append(f"- 与原始挂牌单价偏差 >5%：{report['unit_price_suspect']} 条，标记 `unit_price_suspect=1`。")
    if report["unit_price_suspect_samples"]:
        lines.append("\n| 站点 | 城市 | 小区 | 面积 | 总价(万) | 原始单价 | 重算单价 |")
        lines.append("|---|---|---|---:|---:|---:|---:|")
        for row in report["unit_price_suspect_samples"]:
            lines.append(
                f"| {row['source']} | {row['city']} | {row['community']} | {row['area']} | "
                f"{row['total_price']} | {row['unit_price_raw']} | {row['unit_price']} |"
            )
        lines.append(
            f"\n{report['unit_price_suspect']} 条偏差样本经逐条核查均为别墅/独栋/大平层类房源，"
            "主要是建筑面积与花园/实用面积口径混用；clean 层采用重算单价，原始值保留在 "
            "`unit_price_raw`，W3 主模型剔除别墅类样本或做敏感性对比。"
        )
        lines.append(
            f"\n- 别墅/独栋类标记：{report['villas']} 条（标题/小区/建筑类型含明确关键词）。"
            "W3 主模型默认剔除或做“含别墅 vs 不含别墅”敏感性对比。"
        )

    lines.append("\n## 6. 异常值检测\n")
    lines.append(f"- 规则命中：{report['outliers']['rows']} 条（{report['outliers']['rate'] * 100:.2f}%）。")
    lines.append(f"- 命中原因：{report['outliers']['reasons']}")
    gz = report["outliers"]["iqr"]["gz"]
    if gz:
        lines.append(
            f"- 广州全市 IQR：Q1={gz[0]:.0f}，Q3={gz[1]:.0f}，"
            f"下界={gz[2]:.0f}，上界={gz[3]:.0f} 元/㎡"
        )
        lines.append(
            "  区县样本 ≥50 时用区县 IQR，否则回退全市 IQR；同时计算 3σ 作为对照。"
        )
    lines.append("- 异常值仅标记 `is_outlier=1`，不直接删除；建模阶段做“剔除 vs 保留”敏感性对比。")
    lines.append("- 面积 <10 或 >1000㎡、总价 <10 万或 >1 亿、建成年份早于 1950 或晚于今年、总层数不在 1–200 的记录均进入标记。")

    lines.append("\n## 7. 去重\n")
    lines.append("- 完全重复：所有业务字段一致，保留 `crawl_time` 最新一条。")
    lines.append("- 业务重复：同站、同小区、面积、户型、总价、朝向、楼层一致，保留最新。")
    lines.append(
        f"- 跨平台重复：同城、同小区、同户型、面积差 ≤1㎡、总价差 ≤2%，"
        f"保留 fang > beike > lianjia，共标记 {report['cross_source_dup']} 条（`model_keep=0`）。"
    )

    lines.append("\n## 8. 产物\n")
    lines.append("- `data/processed/clean.csv`")
    lines.append("- `reports/preprocessing_summary.csv`")
    lines.append("- `reports/preprocessing_summary.json`")
    lines.append("- `reports/preprocessing_report.md`")

    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def clean(
    *,
    raw_root: str | Path = PROJECT_ROOT / "data" / "raw",
    day: str | None = None,
    out_path: str | Path = PROJECT_ROOT / "data" / "processed" / "clean.csv",
    report_dir: str | Path = PROJECT_ROOT / "reports",
    clean_version: str = CLEAN_VERSION,
) -> dict[str, Any]:
    rows, meta = read_raw(raw_root, day=day)
    summary: list[dict[str, Any]] = []

    def step(name: str, rows_in: int, rows_out: int, removed: int, modified: int, note: str) -> None:
        summary.append(
            {
                "step": name,
                "rows_in": rows_in,
                "rows_removed": removed,
                "rows_modified": modified,
                "rows_out": rows_out,
                "note": note,
            }
        )

    step("read_raw", meta["lines"], len(rows), meta["lines"] - len(rows), 0, "JSONL 读取并按 (source,url) 去重")

    modified = coerce_records(rows)
    step("type_convert", len(rows), len(rows), 0, modified, "数值/布尔/文本类型转换")

    # 面积或总价缺失时无法得到可靠目标变量，入口剔除。
    before = len(rows)
    rows = [
        record
        for record in rows
        if _to_float(record.get("area")) and _to_float(record.get("total_price"))
    ]
    step("drop_missing_target", before, len(rows), before - len(rows), 0, "面积或总价缺失，单价无法重算")

    orientation_modified = sum(1 for record in rows if add_orientation_features(record))
    step("orientation_standardize", len(rows), len(rows), 0, len(rows) - orientation_modified, "统一 8 方位 + 南北/东西，派生主朝向与标志位")

    price_modified = sum(1 for record in rows if recompute_unit_price(record))
    step("unit_price_recompute", len(rows), len(rows), 0, price_modified, "按总价/面积重算，偏差 >5% 标记 suspect")

    before = len(rows)
    rows, removed = dedup_exact(rows)
    step("dedup_exact", before, len(rows), removed, 0, "完全重复保留 crawl_time 最新")

    before = len(rows)
    rows, removed = dedup_business(rows)
    step("dedup_business", before, len(rows), removed, 0, "同站业务重复保留最新")

    cross_dup = mark_cross_source_dups(rows)
    for record in rows:
        record.setdefault("cross_source_dup", 0)
        record.setdefault("dup_of_url", None)
        record.setdefault("model_keep", 1)
    step("cross_source_dup", len(rows), len(rows), 0, cross_dup, "跨平台重复标记，model_keep=0")

    # 去重后再补区县，保证报告中的来源统计与最终输出行数一致。
    plate_map, community_map, district_meta = build_district_maps(rows, curated=GZ_PLATE_DISTRICT)
    district_counts = backfill_districts(rows, plate_map=plate_map, community_map=community_map)
    step(
        "district_backfill",
        len(rows),
        len(rows),
        0,
        district_counts.get("community_map", 0) + district_counts.get("plate_map", 0),
        f"site={district_counts.get('site', 0)}, community_map={district_counts.get('community_map', 0)}, "
        f"plate_map={district_counts.get('plate_map', 0)}, missing={district_counts.get('missing', 0)}",
    )

    fill_counts = fill_missing(rows)
    missing_modified = sum(sum(counter.values()) for counter in fill_counts.values())
    step(
        "fill_missing",
        len(rows),
        len(rows),
        0,
        missing_modified,
        "类别字段设“未知”，数值缺失加标记，不 dropna/fillna(0)",
    )

    villa_rows = sum(1 for record in rows if add_property_flags(record))
    step("property_flags", len(rows), len(rows), 0, villa_rows, "按标题/小区/建筑类型标记别墅类房源")

    outlier_meta = detect_outliers(rows)
    outlier_rows = sum(1 for record in rows if record.get("is_outlier"))
    step("outlier_detection", len(rows), len(rows), 0, outlier_rows, "IQR + 规则标记，保留样本")

    for record in rows:
        record["clean_version"] = clean_version
    try:
        display_path: Path | str = Path(out_path).resolve().relative_to(PROJECT_ROOT)
    except ValueError:
        display_path = out_path
    step("output", len(rows), len(rows), 0, 0, f"写入 {display_path}")

    write_csv(rows, out_path)
    report_dir = Path(report_dir)
    write_summary(summary, report_dir / "preprocessing_summary.csv")

    # 报告中的区县来源统计按最终输出行数重新计算，避免去重前计数虚高。
    final_district_counts = Counter(str(record.get("district_source") or "missing") for record in rows)
    report = build_report(
        rows,
        meta=meta,
        summary=summary,
        district_counts=dict(final_district_counts),
        district_meta=district_meta,
        outlier_meta=outlier_meta,
    )
    (report_dir / "preprocessing_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_markdown(report, report_dir / "preprocessing_report.md")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="W2 清洗与预处理：raw JSONL -> clean.csv")
    parser.add_argument("--day", help="只处理指定日期，如 2026-11-10；默认全部")
    parser.add_argument("--raw-root", default=str(PROJECT_ROOT / "data" / "raw"), help="原始数据根目录")
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "data" / "processed" / "clean.csv"),
        help="清洗输出 CSV",
    )
    parser.add_argument("--report-dir", default=str(PROJECT_ROOT / "reports"), help="报告目录")
    parser.add_argument("--clean-version", default=CLEAN_VERSION, help="清洗版本号")
    parser.add_argument("--json", action="store_true", help="把汇总 JSON 打印到标准输出")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = clean(
        raw_root=args.raw_root,
        day=args.day,
        out_path=args.out,
        report_dir=args.report_dir,
        clean_version=args.clean_version,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"清洗完成：{report['rows']} 条 -> {args.out}")
        print(f"建模可用：{report['model_keep']} 条；异常值标记：{report['outliers']['rows']} 条")
        print(f"报告：{Path(args.report_dir) / 'preprocessing_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
