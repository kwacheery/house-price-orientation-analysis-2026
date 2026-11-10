"""W2 清洗逻辑单元测试（不联网、只读内存数据）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.clean import (
    GZ_PLATE_DISTRICT,
    add_orientation_features,
    add_property_flags,
    backfill_districts,
    build_district_maps,
    dedup_business,
    dedup_exact,
    detect_outliers,
    fill_missing,
    iqr_bounds,
    mark_cross_source_dups,
    read_raw,
    recompute_unit_price,
)


def make_record(**overrides):
    record = {
        "source": "fang",
        "source_id": "1",
        "city": "广州",
        "city_code": "gz",
        "district": "天河区",
        "plate": "珠江新城",
        "community": "测试花园",
        "title": "测试房源",
        "url": "https://example.com/1",
        "layout": "3室2厅",
        "rooms": 3,
        "halls": 2,
        "bathrooms": None,
        "area": 100.0,
        "orientation_raw": "南",
        "orientation": "南",
        "floor_raw": "中楼层(共18层)",
        "floor_level": "中",
        "total_floors": 18,
        "build_year": 2010,
        "decoration": "精装",
        "elevator": True,
        "building_type": "板楼",
        "total_price": 500.0,
        "unit_price": 50000.0,
        "unit_price_calc": 50000.0,
        "unit_price_mismatch": False,
        "page": 1,
        "list_url": "https://example.com/list",
        "crawl_time": "2026-11-10T10:00:00+08:00",
    }
    record.update(overrides)
    return record


def test_orientation_features_south_north():
    record = make_record(orientation="南北")
    assert add_orientation_features(record) is True
    assert record["orientation_main"] == "南"
    assert record["is_south"] == 1
    assert record["is_south_north"] == 1
    assert record["orientation_count"] == 2
    assert record["orientation_missing"] == 0


def test_orientation_features_diagonal_and_unknown():
    record = make_record(orientation="西南向")
    add_orientation_features(record)
    assert record["orientation"] == "西南"
    assert record["orientation_main"] == "西南"
    assert record["is_south"] == 0
    assert record["orientation_count"] == 1

    missing = make_record(orientation=None, orientation_raw=None)
    assert add_orientation_features(missing) is False
    assert missing["orientation_main"] == "未知"
    assert missing["orientation_missing"] == 1


def test_recompute_unit_price_flags_mismatch():
    record = make_record(area=400.0, total_price=5800.0, unit_price=182965.0)
    changed = recompute_unit_price(record)
    assert changed is True
    assert record["unit_price"] == 145000.0
    assert record["unit_price_raw"] == 182965.0
    assert record["unit_price_suspect"] is True
    assert record["unit_price_mismatch"] is True


def test_recompute_unit_price_consistent():
    record = make_record(area=100.0, total_price=500.0, unit_price=50000.0)
    recompute_unit_price(record)
    assert record["unit_price"] == 50000.0
    assert record["unit_price_suspect"] is False


def test_add_property_flags_villa_and_normal():
    villa = make_record(title="二沙岛独栋别墅 大花园", community="宏城花园")
    assert add_property_flags(villa) is True
    assert villa["is_villa"] == 1

    normal = make_record(title="花园小区三房", community="测试花园")
    assert add_property_flags(normal) is False
    assert normal["is_villa"] == 0

    by_type = make_record(title="南北通透", community="某小区", building_type="别墅")
    assert add_property_flags(by_type) is True


def test_fill_missing_explicit_unknown():
    record = make_record(
        community=None,
        decoration=None,
        floor_level=None,
        elevator=None,
        build_year=None,
        orientation=None,
        orientation_raw=None,
    )
    add_orientation_features(record)
    fill_missing([record])
    assert record["community"] == "未知小区"
    assert record["decoration"] == "未知"
    assert record["floor_level"] == "未知"
    assert record["elevator"] == "未知"
    assert record["orientation"] == "未知"
    assert record["age_missing"] == 1
    assert record["age"] is None
    assert record["floor_ratio"] is None


def test_fill_missing_keeps_known_values():
    from datetime import datetime

    record = make_record()
    fill_missing([record])
    assert record["community"] == "测试花园"
    assert record["decoration"] == "精装"
    assert record["elevator"] == "有"
    assert record["age"] == datetime.now().year - 2010
    assert record["age_missing"] == 0
    assert record["floor_ratio"] == 0.5


def test_district_backfill_prefers_community_then_plate():
    rows = [
        make_record(district=None, community="测试花园", plate="未知板块"),
        make_record(district=None, community="另一个小区", plate="珠江新城"),
        make_record(district=None, community="没有映射", plate="没有映射"),
        make_record(district=None, community="跨城同名", plate="珠江新城", city_code="sz", city="深圳"),
    ]
    plate_map, community_map, meta = build_district_maps(
        [make_record(district="天河区", community="测试花园", plate="珠江新城")]
    )
    assert meta["curated_added"] == 0
    counts = backfill_districts(rows, plate_map=plate_map, community_map=community_map)
    assert rows[0]["district"] == "天河区"
    assert rows[0]["district_source"] == "community_map"
    assert rows[1]["district"] == "天河区"
    assert rows[1]["district_source"] == "plate_map"
    assert rows[2]["district"] is None
    assert rows[2]["district_source"] == "missing"
    assert rows[3]["district_source"] == "missing"
    assert counts["missing"] == 2


def test_curated_plate_map_covers_known_lianjia_plates():
    for plate in ("中大", "大坦沙", "香雪", "进港大道", "黄埔永和"):
        assert plate in GZ_PLATE_DISTRICT


def test_dedup_exact_keeps_latest():
    older = make_record(url="https://example.com/1", crawl_time="2026-11-10T09:00:00+08:00")
    newer = make_record(url="https://example.com/1", crawl_time="2026-11-10T10:00:00+08:00")
    rows, removed = dedup_exact([older, newer])
    assert removed == 1
    assert len(rows) == 1
    assert rows[0]["crawl_time"] == newer["crawl_time"]


def test_dedup_business_keeps_latest_and_skips_unknown_community():
    older = make_record(crawl_time="2026-11-10T09:00:00+08:00")
    newer = make_record(crawl_time="2026-11-10T10:00:00+08:00")
    different_source = make_record(source="beike", crawl_time="2026-11-10T11:00:00+08:00")
    unknown_a = make_record(community="未知小区", url="https://example.com/a")
    unknown_b = make_record(community="未知小区", url="https://example.com/b")

    rows, removed = dedup_business([older, newer, different_source, unknown_a, unknown_b])
    assert removed == 1
    assert len(rows) == 4
    assert unknown_a in rows and unknown_b in rows


def test_cross_source_dup_marked_not_removed():
    fang = make_record(source="fang", area=100.0, total_price=500.0)
    beike = make_record(
        source="beike",
        url="https://example.com/bk",
        area=100.5,
        total_price=505.0,
        crawl_time="2026-11-10T11:00:00+08:00",
    )
    other_city = make_record(source="beike", city_code="sz", city="深圳", url="https://example.com/sz")
    rows = [fang, beike, other_city]
    marked = mark_cross_source_dups(rows)
    assert marked == 1
    assert fang["cross_source_dup"] == 0
    assert beike["cross_source_dup"] == 1
    assert beike["model_keep"] == 0
    assert beike["dup_of_url"] == fang["url"]
    assert other_city["cross_source_dup"] == 0


def test_iqr_bounds_and_outlier_flag():
    values = [100, 100, 100, 100, 100, 100, 100, 100, 100, 1000]
    bounds = iqr_bounds(values)
    assert bounds is not None
    q1, q3, low, high = bounds
    assert q1 == pytest.approx(100)
    assert q3 == pytest.approx(100)
    assert low == pytest.approx(100)
    assert high == pytest.approx(100)

    rows = [make_record(area=2000.0, unit_price=50000.0)]
    meta = detect_outliers(rows, year=2026)
    assert rows[0]["is_outlier"] == 1
    assert "area_range" in rows[0]["outlier_reason"]
    assert "area_range" in meta["reasons"]


def test_read_raw_dedups_by_source_and_url(tmp_path: Path):
    path = tmp_path / "fang" / "gz" / "2026-11-10.jsonl"
    path.parent.mkdir(parents=True)
    import json

    older = make_record(crawl_time="2026-11-10T09:00:00+08:00")
    newer = make_record(crawl_time="2026-11-10T10:00:00+08:00")
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in (older, newer)) + "\n",
        encoding="utf-8",
    )
    rows, meta = read_raw(tmp_path, day="2026-11-10")
    assert meta["lines"] == 2
    assert meta["duplicate_url"] == 1
    assert len(rows) == 1
    assert rows[0]["crawl_time"] == newer["crawl_time"]
