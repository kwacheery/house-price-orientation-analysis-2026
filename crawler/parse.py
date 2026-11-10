"""链家 / 贝壳 / 房天下列表页字段解析。

统一输出计划书 4.3 的数据字典字段。列表页缺失的建成年份、装修、电梯等
保留为 ``None``，由清洗阶段统一设为“未知”，不做无依据填充。
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import clean_url, now_iso, parse_number

# 广州 11 区：房天下采集任务写入全称；链家/贝壳位置只显示“天河”，这里统一补“区”。
GZ_DISTRICT_FULL = {
    "天河": "天河区",
    "越秀": "越秀区",
    "荔湾": "荔湾区",
    "海珠": "海珠区",
    "白云": "白云区",
    "番禺": "番禺区",
    "黄埔": "黄埔区",
    "花都": "花都区",
    "南沙": "南沙区",
    "从化": "从化区",
    "增城": "增城区",
}

ORIENTATION_ORDER = ("南北", "东西", "东南", "东北", "西南", "西北", "东", "南", "西", "北")

DECORATION_MAP = {
    "毛坯": "毛坯",
    "简装": "简装",
    "简装修": "简装",
    "精装": "精装",
    "精装修": "精装",
    "豪装": "豪装",
    "豪华装修": "豪装",
}

BUILDING_TYPES = ("板塔结合", "板楼", "塔楼", "平房", "别墅")

# 用于 QA 的字段清单（顺序即报告顺序）
FIELD_ORDER = [
    "source",
    "source_id",
    "city",
    "city_code",
    "district",
    "plate",
    "community",
    "title",
    "url",
    "layout",
    "rooms",
    "halls",
    "bathrooms",
    "area",
    "orientation_raw",
    "orientation",
    "floor_raw",
    "floor_level",
    "total_floors",
    "build_year",
    "decoration",
    "elevator",
    "building_type",
    "total_price",
    "unit_price",
    "unit_price_calc",
    "unit_price_mismatch",
    "page",
    "list_url",
    "crawl_time",
]

CORE_FIELDS = ["title", "url", "district", "community", "area", "orientation", "total_price", "unit_price"]

SEGMENT_SPLIT = re.compile(r"[|｜/、，,;；]+")
LAYOUT_RE = re.compile(r"(\d+)\s*室\s*(\d+)\s*厅(?:\s*(\d+)\s*卫)?")
AREA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:㎡|平米|平方米|平)")
FLOOR_RE = re.compile(r"(低|中|高)\s*(?:楼层|层)\s*(?:[（(]\s*)?共\s*(\d+)\s*层")
YEAR_RE = re.compile(r"((?:19|20)\d{2})\s*年(?:建|建造|建成|次新房)?")
ORIENTATION_RE = re.compile(r"^(?:朝|全)?([东南西北]{1,3})(?:向)?$")


def normalize_district(name: str | None, city_code: str) -> str | None:
    text = (name or "").strip()
    if not text:
        return None
    if city_code == "gz":
        return GZ_DISTRICT_FULL.get(text, text)
    return text


def normalize_orientation(raw: str | None) -> str | None:
    """把“西南向 / 南 北 / 全南 / 南北通透”归一为 8 方位 + 南北/东西。"""
    if not raw:
        return None
    text = re.sub(r"\s+", "", raw)
    text = text.replace("朝向", "").replace("向", "")
    if not text:
        return None
    if "南北" in text or ("南" in text and "北" in text):
        return "南北"
    if "东西" in text or ("东" in text and "西" in text):
        return "东西"
    for token in ("东南", "东北", "西南", "西北"):
        if token in text:
            return token
    for token in ("东", "南", "西", "北"):
        if token in text:
            return token
    return None


def split_segments(text: str | None) -> list[str]:
    if not text:
        return []
    return [segment.strip() for segment in SEGMENT_SPLIT.split(text) if segment.strip()]


def _parse_layout(segment: str) -> tuple[str, int, int, int | None] | None:
    match = LAYOUT_RE.search(segment)
    if not match:
        return None
    rooms = int(match.group(1))
    halls = int(match.group(2))
    bathrooms = int(match.group(3)) if match.group(3) else None
    return f"{rooms}室{halls}厅" + (f"{bathrooms}卫" if bathrooms else ""), rooms, halls, bathrooms


def _parse_orientation_segment(segment: str) -> str | None:
    cleaned = segment.replace(" ", "")
    if "南北通透" in cleaned:
        return "南北"
    match = ORIENTATION_RE.fullmatch(cleaned)
    if not match:
        return None
    candidate = match.group(1)
    return normalize_orientation(candidate)


def parse_house_info(text: str | None) -> dict[str, Any]:
    """解析列表页信息串，如 ``3室2厅 | 89.5㎡ | 南 | 精装 | 中楼层(共18层) | 2010年建 | 板楼``。"""
    text = re.sub(r"\s+", " ", text or "").strip()
    result: dict[str, Any] = {
        "layout": None,
        "rooms": None,
        "halls": None,
        "bathrooms": None,
        "area": None,
        "orientation_raw": None,
        "orientation": None,
        "floor_raw": None,
        "floor_level": None,
        "total_floors": None,
        "build_year": None,
        "decoration": None,
        "elevator": None,
        "building_type": None,
    }
    for segment in split_segments(text):
        if result["layout"] is None:
            layout = _parse_layout(segment)
            if layout:
                result["layout"], result["rooms"], result["halls"], result["bathrooms"] = layout
                continue
        if result["area"] is None:
            area_match = AREA_RE.search(segment)
            if area_match:
                result["area"] = float(area_match.group(1))
                continue
        if result["orientation_raw"] is None:
            orientation = _parse_orientation_segment(segment)
            if orientation:
                result["orientation_raw"] = segment
                result["orientation"] = orientation
                continue
        if result["floor_raw"] is None:
            floor_match = FLOOR_RE.search(segment)
            if floor_match:
                result["floor_raw"] = segment
                result["floor_level"] = floor_match.group(1)
                result["total_floors"] = int(floor_match.group(2))
                continue
        if result["build_year"] is None:
            year_match = YEAR_RE.search(segment)
            if year_match:
                result["build_year"] = int(year_match.group(1))
                continue
        if result["decoration"] is None and segment in DECORATION_MAP:
            result["decoration"] = DECORATION_MAP[segment]
            continue
        if result["building_type"] is None and segment in BUILDING_TYPES:
            result["building_type"] = segment
            continue

    # 无分隔符或分隔符不规范时，用整串正则兜底。
    if result["layout"] is None:
        layout = _parse_layout(text or "")
        if layout:
            result["layout"], result["rooms"], result["halls"], result["bathrooms"] = layout
    if result["area"] is None:
        area_match = AREA_RE.search(text or "")
        if area_match:
            result["area"] = float(area_match.group(1))
    if result["build_year"] is None:
        year_match = YEAR_RE.search(text or "")
        if year_match:
            result["build_year"] = int(year_match.group(1))
    if result["decoration"] is None:
        for key, value in DECORATION_MAP.items():
            if key in (text or ""):
                result["decoration"] = value
                break

    result["elevator"] = parse_elevator(text)
    return result


def parse_elevator(text: str | None) -> bool | None:
    if not text:
        return None
    if "无电梯" in text:
        return False
    if "有电梯" in text or "电梯" in text:
        return True
    return None


def _new_record(
    *,
    source: str,
    city: str,
    city_code: str,
    district: str | None,
    page: int | None,
    list_url: str | None,
) -> dict[str, Any]:
    record = {field: None for field in FIELD_ORDER}
    record.update(
        {
            "source": source,
            "city": city,
            "city_code": city_code,
            "district": normalize_district(district, city_code),
            "page": page,
            "list_url": clean_url(list_url) if list_url else None,
            "crawl_time": now_iso(),
        }
    )
    return record


def finalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """补齐派生字段：总价/面积重算单价，并标记 5% 以上不一致。"""
    area = record.get("area")
    total = record.get("total_price")
    unit = record.get("unit_price")
    if area and total:
        calculated = round(float(total) * 10000 / float(area), 2)
        record["unit_price_calc"] = calculated
        if unit is None:
            record["unit_price"] = calculated
            record["unit_price_mismatch"] = None
        else:
            record["unit_price_mismatch"] = abs(calculated - float(unit)) / float(unit) > 0.05
    else:
        record["unit_price_calc"] = None
        record["unit_price_mismatch"] = None
    return record


def _source_id_from_url(url: str) -> str | None:
    path = urlparse(url).path.rstrip("/")
    stem = path.split("/")[-1]
    return stem.rsplit(".", 1)[0] or None


def _price_from_text(text: str | None) -> tuple[float | None, float | None]:
    """从房天下价格区文本中拆总价（万）与单价（元/㎡）。"""
    if not text:
        return None, None
    total_match = re.search(r"([\d.,]+)\s*万", text)
    unit_match = re.search(r"([\d,.]+)\s*元\s*/\s*(?:㎡|平|平米)", text)
    total = parse_number(total_match.group(1)) if total_match else None
    unit = parse_number(unit_match.group(1)) if unit_match else None
    return total, unit


def parse_fang_html(
    html: str,
    *,
    district: str | None,
    page: int | None = None,
    list_url: str | None = None,
) -> list[dict[str, Any]]:
    """解析房天下广州列表页。区县由采集任务传入，避免从板块反推。"""
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, Any]] = []
    for card in soup.select("dl.clearfix"):
        title_link = card.select_one("h4 a[href]")
        if not title_link:
            continue

        url = clean_url(urljoin("https://gz.esf.fang.com", title_link["href"]))
        record = _new_record(
            source="fang",
            city="广州",
            city_code="gz",
            district=district,
            page=page,
            list_url=list_url,
        )

        source_id = None
        raw_bg = card.get("data-bg")
        if raw_bg:
            try:
                source_id = str(json.loads(raw_bg).get("houseid") or "") or None
            except (json.JSONDecodeError, AttributeError):
                source_id = None

        info_el = card.select_one("p.tel_shop")
        if info_el is not None:
            for junk in info_el.select(".people_name"):
                junk.decompose()
        info_text = info_el.get_text(" ", strip=True) if info_el is not None else ""

        community_el = card.select_one("p.add_shop a")
        plate_el = card.select_one("p.add_shop span")
        price_el = card.select_one("dd.price_right")
        total, unit = _price_from_text(price_el.get_text(" ", strip=True) if price_el else "")
        # 房天下把“板块 + 街道地址”放在同一个 span 里，只保留板块名，避免存入门牌级地址。
        plate = plate_el.get_text(" ", strip=True).split()[0] if plate_el and plate_el.get_text(strip=True) else None

        record.update(
            {
                "source_id": source_id or _source_id_from_url(url),
                "plate": plate,
                "community": community_el.get_text(" ", strip=True) if community_el else None,
                "title": re.sub(r"\s+", " ", title_link.get_text(" ", strip=True)),
                "url": url,
                "total_price": total,
                "unit_price": unit,
            }
        )
        record.update(parse_house_info(info_text))
        rows.append(finalize_record(record))
    return rows


def _parse_lianjia_position(card: Any, city_code: str) -> tuple[str | None, str | None, str | None]:
    """返回 (district, plate, community)。

    城市首页的 positionInfo 结构不稳定：链家是“小区 + 板块”，贝壳只有小区，
    区县通常不在卡片里。不能把小区名误当区县，缺失就留 None。
    """
    position_el = card.select_one(".positionInfo")
    if position_el is None:
        return None, None, None

    district: str | None = None
    plate: str | None = None
    community: str | None = None
    area_links: list[tuple[str, str]] = []
    for link in position_el.select("a[href]"):
        text = link.get_text(" ", strip=True)
        href = link.get("href") or ""
        if "/xiaoqu/" in href:
            community = community or text
        elif "/ershoufang/" in href:
            area_links.append((text, href))

    # 两个区域链接时（区县 + 板块），第一个是区县；只有一个时按后缀判断。
    if len(area_links) >= 2:
        district = normalize_district(area_links[0][0], city_code)
        plate = area_links[1][0]
    elif len(area_links) == 1:
        text = area_links[0][0]
        if text.endswith(("区", "县", "市", "旗")):
            district = normalize_district(text, city_code)
        else:
            plate = text
    return district, plate, community


def _parse_alt_location(
    card: Any,
    *,
    city: str,
    city_code: str,
) -> tuple[str | None, str | None]:
    """贝壳卡片 img[alt] 形如 ``标题-广州番禺区万博二手房``，可补区县和板块。"""
    image = card.select_one("img[alt]")
    if image is None:
        return None, None
    alt = image.get("alt") or ""
    match = re.search(rf"-{re.escape(city)}(?P<rest>.+?)二手房\s*$", alt)
    if not match:
        return None, None
    rest = match.group("rest").strip()
    district_match = re.match(r"(.+?[区县市旗])(.*)$", rest)
    if not district_match:
        return None, None
    district = normalize_district(district_match.group(1), city_code)
    plate = district_match.group(2).strip() or None
    return district, plate


def parse_lianjia_like_html(
    html: str,
    *,
    source: str,
    city: str,
    city_code: str,
    page: int | None = None,
    list_url: str | None = None,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    """解析链家/贝壳列表页；两站 DOM 结构基本一致。"""
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("ul.sellListContent > li")
    if not cards:
        cards = soup.select("ul.sellListContent li")
    rows: list[dict[str, Any]] = []
    for card in cards:
        title_link = card.select_one(".title a[href]")
        if not title_link:
            continue

        href = title_link["href"]
        url = clean_url(urljoin(base_url or "", href))
        district, plate, position_community = _parse_lianjia_position(card, city_code)
        alt_district, alt_plate = _parse_alt_location(card, city=city, city_code=city_code)
        district = district or alt_district
        plate = plate or alt_plate

        house_info_el = card.select_one(".houseInfo")
        house_text = house_info_el.get_text(" ", strip=True) if house_info_el is not None else ""
        community_el = card.select_one('.houseInfo a[href*="/xiaoqu/"]') or card.select_one(
            '.address a[href*="/xiaoqu/"]'
        )
        community = (
            community_el.get_text(" ", strip=True) if community_el is not None else position_community
        )
        tag_text = " ".join(
            tag.get_text(" ", strip=True) for tag in card.select(".tag span, .tag .taxfree")
        )

        total_el = card.select_one(".totalPrice")
        unit_el = card.select_one(".unitPrice")
        total = parse_number(total_el.get_text(" ", strip=True)) if total_el else None
        unit = parse_number(unit_el.get_text(" ", strip=True)) if unit_el else None

        record = _new_record(
            source=source,
            city=city,
            city_code=city_code,
            district=district,
            page=page,
            list_url=list_url,
        )
        record.update(
            {
                "source_id": _source_id_from_url(url),
                "plate": plate,
                "community": community,
                "title": re.sub(r"\s+", " ", title_link.get_text(" ", strip=True)),
                "url": url,
                "total_price": total,
                "unit_price": unit,
            }
        )
        record.update(parse_house_info(house_text))
        if record["elevator"] is None:
            record["elevator"] = parse_elevator(tag_text)
        rows.append(finalize_record(record))
    return rows
