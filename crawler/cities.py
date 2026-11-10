"""链家 / 贝壳多城市候选列表。

每个元组为 (城市代码, 城市名, 大区)。城市代码即站点的二级域名前缀，
例如 gz -> gz.lianjia.com。部分城市可能不存在或已下线，采集器会跳过并记录。
"""

from __future__ import annotations

from typing import NamedTuple


class City(NamedTuple):
    code: str
    name: str
    region: str


CITY_CANDIDATES: list[City] = [
    # 华南（优先，和广州可比性最强）
    City("gz", "广州", "华南"),
    City("sz", "深圳", "华南"),
    City("fs", "佛山", "华南"),
    City("dg", "东莞", "华南"),
    City("zh", "珠海", "华南"),
    City("zs", "中山", "华南"),
    City("hui", "惠州", "华南"),
    City("jm", "江门", "华南"),
    City("qy", "清远", "华南"),
    City("zq", "肇庆", "华南"),
    City("st", "汕头", "华南"),
    City("zj", "湛江", "华南"),
    City("sg", "韶关", "华南"),
    City("mz", "梅州", "华南"),
    City("hy", "河源", "华南"),
    City("sw", "汕尾", "华南"),
    City("yj", "阳江", "华南"),
    City("mm", "茂名", "华南"),
    City("yunfu", "云浮", "华南"),
    City("haikou", "海口", "华南"),
    City("sanya", "三亚", "华南"),
    City("nn", "南宁", "华南"),
    City("gl", "桂林", "华南"),
    City("liuzhou", "柳州", "华南"),
    # 华东
    City("sh", "上海", "华东"),
    City("hz", "杭州", "华东"),
    City("nj", "南京", "华东"),
    City("su", "苏州", "华东"),
    City("wx", "无锡", "华东"),
    City("cz", "常州", "华东"),
    City("nt", "南通", "华东"),
    City("nb", "宁波", "华东"),
    City("wz", "温州", "华东"),
    City("jx", "嘉兴", "华东"),
    City("jh", "金华", "华东"),
    City("hf", "合肥", "华东"),
    City("xm", "厦门", "华东"),
    City("fz", "福州", "华东"),
    City("qz", "泉州", "华东"),
    City("jn", "济南", "华东"),
    City("qd", "青岛", "华东"),
    City("yt", "烟台", "华东"),
    City("wf", "潍坊", "华东"),
    City("zb", "淄博", "华东"),
    # 华北
    City("bj", "北京", "华北"),
    City("tj", "天津", "华北"),
    City("sjz", "石家庄", "华北"),
    City("ts", "唐山", "华北"),
    City("bd", "保定", "华北"),
    City("lf", "廊坊", "华北"),
    City("hd", "邯郸", "华北"),
    City("ty", "太原", "华北"),
    City("hhht", "呼和浩特", "华北"),
    # 华中
    City("wh", "武汉", "华中"),
    City("cs", "长沙", "华中"),
    City("zz", "郑州", "华中"),
    City("nc", "南昌", "华中"),
    # 西南
    City("cd", "成都", "西南"),
    City("cq", "重庆", "西南"),
    City("km", "昆明", "西南"),
    City("gy", "贵阳", "西南"),
    # 西北
    City("xa", "西安", "西北"),
    City("lz", "兰州", "西北"),
    City("yc", "银川", "西北"),
    City("xining", "西宁", "西北"),
    City("wlmq", "乌鲁木齐", "西北"),
    # 东北
    City("cc", "长春", "东北"),
    City("sy", "沈阳", "东北"),
    City("dl", "大连", "东北"),
    City("hrb", "哈尔滨", "东北"),
]

DEFAULT_TARGET_CITIES = 33


def select_cities(codes: list[str] | None = None, limit: int | None = None) -> list[City]:
    """按代码筛选城市；未指定时按候选顺序返回。"""
    cities = CITY_CANDIDATES
    if codes:
        wanted = {c.strip().lower() for c in codes if c.strip()}
        cities = [c for c in cities if c.code in wanted]
    if limit is not None:
        cities = cities[:limit]
    return list(cities)
