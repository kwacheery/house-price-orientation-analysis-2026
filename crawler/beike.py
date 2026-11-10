"""贝壳多城市首页采集入口。

用法：
    python -m crawler.beike --cities gz,sz,bj --target-cities 3 --max-attempts 3

说明：贝壳仅城市首页可稳定访问，每城 31 条；不请求第 2 页、区县页、筛选页，
不破解验证码。出现登录/验证码/403 时立即停止并记录。
"""

from __future__ import annotations

from typing import Any

from .ershoufang import crawl_city_index as _crawl_city_index
from .ershoufang import main_for

SOURCE = "beike"

__all__ = ["SOURCE", "crawl_city_index", "main_for"]


def crawl_city_index(*args: Any, **kwargs: Any):
    """绑定站点名后的采集入口，便于试点脚本直接调用。"""
    return _crawl_city_index(SOURCE, *args, **kwargs)


if __name__ == "__main__":
    from .ershoufang import main

    main(SOURCE)
