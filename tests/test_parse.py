"""解析层与阻断识别的单元测试（不联网）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crawler.base import BlockedError, HttpFetcher, JsonlSink, detect_block, parse_number
from crawler.parse import (
    finalize_record,
    normalize_district,
    normalize_orientation,
    parse_fang_html,
    parse_house_info,
    parse_lianjia_like_html,
)

FANG_HTML = """
<html><head><title>天河二手房</title></head><body>
<dl class="clearfix" data-bg='{"houseid":"264580737","housetype":"JUHE"}'>
  <dt><a href="/chushou/3_264580737.htm"><img src="x.jpg"></a></dt>
  <dd class="price_right"><span class="red">300万</span><span>33520元/㎡</span></dd>
  <dd>
    <h4><a href="/chushou/3_264580737.htm" title="南北通透三房"><span>南北通透三房</span></a></h4>
    <p class="tel_shop">3室2厅 | 89.5㎡ | 中层 （共18层） | 南北向 | <span class="people_name">某经纪人</span></p>
    <p class="add_shop"><a href="/xiaoqu/1/">测试花园</a><span>珠江新城</span></p>
  </dd>
</dl>
<dl class="clearfix"></dl>
</body></html>
"""

LIANJIA_HTML = """
<html><head><title>广州二手房</title></head><body>
<ul class="sellListContent">
  <li class="clear">
    <div class="title"><a href="https://gz.lianjia.com/ershoufang/123.html">南北通透三房</a></div>
    <div class="address">
      <div class="houseInfo"><a href="/xiaoqu/1/">测试花园</a> | 3室2厅 | 89.5平米 | 南北 | 精装 | 中楼层(共18层) | 2010年建 | 板楼</div>
    </div>
    <div class="positionInfo">
      <a href="/ershoufang/tianhe/">天河</a>
      <a href="/ershoufang/tianhe/zjxc/">珠江新城</a>
    </div>
    <div class="tag"><span>近地铁</span><span>有电梯</span></div>
    <div class="priceInfo">
      <div class="totalPrice"><span>300</span><i>万</i></div>
      <div class="unitPrice"><span>33,520元/平</span></div>
    </div>
  </li>
</ul>
</body></html>
"""


def test_normalize_orientation():
    assert normalize_orientation("南") == "南"
    assert normalize_orientation("西南向") == "西南"
    assert normalize_orientation("南 北") == "南北"
    assert normalize_orientation("南北通透") == "南北"
    assert normalize_orientation("全南") == "南"
    assert normalize_orientation("东西") == "东西"
    assert normalize_orientation("") is None
    assert normalize_orientation("未知") is None


def test_normalize_district():
    assert normalize_district("天河", "gz") == "天河区"
    assert normalize_district("天河区", "gz") == "天河区"
    assert normalize_district("朝阳", "bj") == "朝阳"
    assert normalize_district(None, "gz") is None


def test_parse_number():
    assert parse_number("300万") == 300
    assert parse_number("33,520元/㎡") == 33520
    assert parse_number("89.5㎡") == 89.5
    assert parse_number("暂无") is None


def test_parse_house_info_full():
    info = parse_house_info(
        "测试花园 | 3室2厅1卫 | 89.5㎡ | 西南向 | 精装 | 中楼层(共18层) | 2010年建 | 板楼 | 有电梯"
    )
    assert info["layout"] == "3室2厅1卫"
    assert info["rooms"] == 3
    assert info["halls"] == 2
    assert info["bathrooms"] == 1
    assert info["area"] == 89.5
    assert info["orientation_raw"] == "西南向"
    assert info["orientation"] == "西南"
    assert info["floor_level"] == "中"
    assert info["total_floors"] == 18
    assert info["build_year"] == 2010
    assert info["decoration"] == "精装"
    assert info["elevator"] is True
    assert info["building_type"] == "板楼"


def test_parse_fang_html():
    rows = parse_fang_html(
        FANG_HTML, district="天河区", page=1, list_url="https://gz.esf.fang.com/house-a073/"
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "fang"
    assert row["city"] == "广州"
    assert row["district"] == "天河区"
    assert row["plate"] == "珠江新城"
    assert row["community"] == "测试花园"
    assert row["source_id"] == "264580737"
    assert row["layout"] == "3室2厅"
    assert row["area"] == 89.5
    assert row["orientation"] == "南北"
    assert row["floor_level"] == "中"
    assert row["total_floors"] == 18
    assert row["total_price"] == 300
    assert row["unit_price"] == 33520
    assert row["unit_price_calc"] == pytest.approx(33519.55, abs=0.01)
    assert row["unit_price_mismatch"] is False
    assert "某经纪人" not in json.dumps(row, ensure_ascii=False)


def test_parse_lianjia_like_html():
    rows = parse_lianjia_like_html(
        LIANJIA_HTML,
        source="lianjia",
        city="广州",
        city_code="gz",
        page=1,
        list_url="https://gz.lianjia.com/ershoufang/",
        base_url="https://gz.lianjia.com",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "lianjia"
    assert row["district"] == "天河区"
    assert row["plate"] == "珠江新城"
    assert row["community"] == "测试花园"
    assert row["layout"] == "3室2厅"
    assert row["area"] == 89.5
    assert row["orientation"] == "南北"
    assert row["decoration"] == "精装"
    assert row["build_year"] == 2010
    assert row["elevator"] is True
    assert row["building_type"] == "板楼"
    assert row["total_price"] == 300
    assert row["unit_price"] == 33520
    assert row["unit_price_mismatch"] is False


def test_finalize_record_recomputes_missing_unit_price():
    record = {
        "area": 100.0,
        "total_price": 500.0,
        "unit_price": None,
        "unit_price_calc": None,
        "unit_price_mismatch": None,
    }
    finalize_record(record)
    assert record["unit_price"] == 50000.0
    assert record["unit_price_calc"] == 50000.0
    assert record["unit_price_mismatch"] is None


def test_detect_block():
    assert detect_block(
        final_url="https://clogin.lianjia.com/login?service=x",
        title="登录",
        text="用户登录",
        status=200,
    )
    assert detect_block(
        final_url="https://hip.lianjia.com/captcha?location=x",
        title="CAPTCHA",
        text="人机验证",
        status=200,
    )
    assert detect_block(
        final_url="https://gz.lianjia.com/ershoufang/",
        title="广州二手房房源_广州二手房出售-广州链家",
        text="首页 二手房 新房 租房",
        status=200,
    ) is None
    assert detect_block(final_url="https://gz.esf.fang.com/", title="", text="", status=403) == "http_403"


def test_jsonl_sink_dedup(tmp_path: Path):
    path = tmp_path / "raw.jsonl"
    with JsonlSink(path) as sink:
        assert sink.write({"url": "https://example.com/1", "title": "A"}) is True
        assert sink.write({"url": "https://example.com/1", "title": "A"}) is False
        assert sink.write({"url": "https://example.com/2", "title": "B"}) is True
        assert sink.written == 2
        assert sink.duplicates == 1
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    assert all(line.get("crawl_time") for line in lines)


def test_blocked_error_fields():
    error = BlockedError("https://x", "http_429", final_url="https://x/y", status=429)
    assert error.reason == "http_429"
    assert error.status == 429


def test_http_fetcher_headers():
    fetcher = HttpFetcher(min_delay=0, max_delay=0)
    try:
        assert "User-Agent" in fetcher.session.headers
    finally:
        fetcher.close()
