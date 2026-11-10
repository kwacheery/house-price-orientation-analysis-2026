"""采集基础设施：限速、重试、阻断识别、浏览器/HTTP 抓取、JSONL 落盘、统计。

设计原则（对应计划书 4.4 / 4.5）：
- 顺序采集，请求间隔随机 1–3 秒，单站点不并发。
- 失败重试最多 3 次，退避等待；403/429/验证码/登录跳转不重试、不绕过。
- 原始数据只追加：``data/raw/{source}/{city}/{date}.jsonl``，按 ``url`` 去重。
- 不采集姓名、电话、微信、门牌号等个人隐私字段。
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse

import requests

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# URL 中出现的阻断特征（登录、验证码、反爬回调）
BLOCK_URL_PATTERNS = (
    "clogin.",
    "/login",
    "login?",
    "captcha",
    "verifycode",
    "antibot",
    "esfcommon-captcha",
    "callback.58.com",
)

# 页面标题中的阻断特征
BLOCK_TITLE_MARKERS = (
    "CAPTCHA",
    "安全验证",
    "人机验证",
    "请输入验证码",
    "请完成安全验证",
)

# 正文开头的阻断特征
BLOCK_TEXT_PREFIXES = (
    "用户登录",
    "请完成安全验证",
    "访问已被拦截",
    "人机验证",
    "请输入验证码",
)

# 正文中任意位置出现即可判定的强阻断特征
BLOCK_TEXT_MARKERS = (
    "访问已被拦截",
    "点击按钮开始验证",
    "请完成安全验证",
)

BLOCK_HTTP_STATUS = {403, 429, 521}


class CrawlError(Exception):
    """采集异常基类。"""


class FetchError(CrawlError):
    """网络/解析层面的可重试异常。"""


class BlockedError(CrawlError):
    """站点阻断：登录、验证码、403/429 等。出现后应停止该站点。"""

    def __init__(
        self,
        url: str,
        reason: str,
        *,
        final_url: str | None = None,
        status: int | None = None,
    ) -> None:
        self.url = url
        self.reason = reason
        self.final_url = final_url
        self.status = status
        super().__init__(f"站点阻断 [{reason}] {final_url or url}")


def now_iso() -> str:
    """本地时区 ISO 时间，精确到秒。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def clean_url(url: str) -> str:
    """去掉查询串和锚点，保留稳定可去重的房源链接。"""
    parts = urlparse(url)
    return urlunparse((parts.scheme, parts.netloc, parts.path, "", "", ""))


def parse_number(text: str | None) -> float | None:
    """从“2400万”“98,091元/㎡”等文本中提取第一个数字。"""
    if not text:
        return None
    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(text))
    if not match:
        return None
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return None


def detect_block(
    *,
    final_url: str | None,
    title: str | None,
    text: str | None,
    status: int | None,
) -> str | None:
    """判断当前响应是否被登录/验证码/风控拦截，返回原因或 None。"""
    if status in BLOCK_HTTP_STATUS:
        return f"http_{status}"

    url_lower = (final_url or "").lower()
    for pattern in BLOCK_URL_PATTERNS:
        if pattern in url_lower:
            return f"url:{pattern}"

    title_text = (title or "").strip()
    if title_text:
        upper = title_text.upper()
        if "CAPTCHA" in upper:
            return "title:captcha"
        for marker in BLOCK_TITLE_MARKERS:
            if marker in title_text:
                return f"title:{marker}"
        if title_text in {"登录", "用户登录"} or title_text.endswith("- 登录"):
            return "title:login"
        if title_text.startswith("登录-"):
            return "title:login"

    head = re.sub(r"\s+", " ", text or "").strip()[:300]
    for prefix in BLOCK_TEXT_PREFIXES:
        if head.startswith(prefix):
            return f"text:{prefix}"
    sample = (text or "")[:3000]
    for marker in BLOCK_TEXT_MARKERS:
        if marker in sample:
            return f"text:{marker}"
    return None


class RateLimiter:
    """顺序限速器：两次请求之间随机等待 ``min_delay``–``max_delay`` 秒。"""

    def __init__(self, min_delay: float = 1.5, max_delay: float = 3.0, seed: int | None = None) -> None:
        if min_delay < 0 or max_delay < 0 or max_delay < min_delay:
            raise ValueError("限速参数不合法：需满足 0 <= min_delay <= max_delay")
        self.min_delay = float(min_delay)
        self.max_delay = float(max_delay)
        self._random = random.Random(seed)
        self._last_request: float | None = None

    def wait(self) -> float:
        """在请求前调用。返回本次实际等待的秒数。"""
        target = self._random.uniform(self.min_delay, self.max_delay)
        waited = 0.0
        if self._last_request is not None:
            elapsed = time.monotonic() - self._last_request
            if elapsed < target:
                waited = target - elapsed
                time.sleep(waited)
        self._last_request = time.monotonic()
        return waited

    def mark_request(self) -> None:
        """请求结束后打点，用于计算下一次间隔。"""
        self._last_request = time.monotonic()


@dataclass(slots=True)
class FetchResult:
    url: str
    final_url: str
    status: int | None
    title: str
    html: str
    text: str
    elapsed: float
    fetcher: str

    @property
    def bytes(self) -> int:
        return len(self.html.encode("utf-8", errors="ignore"))


class HttpFetcher:
    """基于 requests 的抓取器，适合房天下等静态列表页。"""

    def __init__(
        self,
        *,
        min_delay: float = 1.5,
        max_delay: float = 3.0,
        timeout: float = 25.0,
        retries: int = 3,
        headers: dict[str, str] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.limiter = RateLimiter(min_delay, max_delay)
        self.timeout = timeout
        self.retries = max(1, retries)
        self.logger = logger or logging.getLogger("crawler")
        self.session = requests.Session()
        self.session.headers.update(headers or DEFAULT_HEADERS)

    def fetch(self, url: str, *, expected_host: str | None = None) -> FetchResult:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self.limiter.wait()
            started = time.monotonic()
            try:
                response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            except requests.RequestException as exc:
                last_error = exc
                self.logger.warning("请求失败（%d/%d）%s：%s", attempt, self.retries, url, exc)
                self._backoff(attempt)
                continue
            finally:
                self.limiter.mark_request()

            elapsed = time.monotonic() - started
            final_url = response.url
            title = self._extract_title(response.text)
            block_reason = detect_block(
                final_url=final_url,
                title=title,
                text=response.text,
                status=response.status_code,
            )
            if block_reason:
                raise BlockedError(url, block_reason, final_url=final_url, status=response.status_code)

            if expected_host:
                host = (urlparse(final_url).hostname or "").lower()
                if host != expected_host.lower():
                    raise FetchError(f"跳转到非目标域名 {host}（期望 {expected_host}）：{final_url}")

            if response.status_code >= 500:
                last_error = FetchError(f"HTTP {response.status_code}")
                self.logger.warning("服务端错误（%d/%d）%s：HTTP %s", attempt, self.retries, url, response.status_code)
                self._backoff(attempt)
                continue

            if response.status_code >= 400:
                last_error = FetchError(f"HTTP {response.status_code}")
                self.logger.warning("请求返回（%d/%d）%s：HTTP %s", attempt, self.retries, url, response.status_code)
                self._backoff(attempt)
                continue

            return FetchResult(
                url=url,
                final_url=final_url,
                status=response.status_code,
                title=title,
                html=response.text,
                text=response.text,
                elapsed=elapsed,
                fetcher="http",
            )

        raise FetchError(f"重试 {self.retries} 次仍失败：{url}（{last_error}）")

    def _backoff(self, attempt: int) -> None:
        time.sleep(min(2 ** attempt, 10))

    @staticmethod
    def _extract_title(html: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html or "", flags=re.S | re.I)
        return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "HttpFetcher":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class BrowserFetcher:
    """基于 Playwright 的抓取器，用于链家/贝壳等需要真实浏览器渲染的页面。"""

    def __init__(
        self,
        *,
        min_delay: float = 2.0,
        max_delay: float = 4.0,
        timeout_ms: int = 45_000,
        retries: int = 2,
        headless: bool = True,
        user_agent: str = DEFAULT_USER_AGENT,
        logger: logging.Logger | None = None,
    ) -> None:
        self.limiter = RateLimiter(min_delay, max_delay)
        self.timeout_ms = timeout_ms
        self.retries = max(1, retries)
        self.headless = headless
        self.user_agent = user_agent
        self.logger = logger or logging.getLogger("crawler")
        self._playwright = None
        self._browser = None
        self._context = None

    def __enter__(self) -> "BrowserFetcher":
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            locale="zh-CN",
            viewport={"width": 1440, "height": 900},
            user_agent=self.user_agent,
        )
        return self

    def __exit__(self, *exc_info: object) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            if self._browser is not None:
                self._browser.close()
            if self._playwright is not None:
                self._playwright.stop()

    def fetch(self, url: str, *, expected_host: str | None = None) -> FetchResult:
        if self._context is None:
            raise CrawlError("BrowserFetcher 需在 with 语句中使用")

        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self.limiter.wait()
            started = time.monotonic()
            page = self._context.new_page()
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                page.wait_for_timeout(1500)
                html = page.content()
                title = page.title()
                try:
                    text = page.inner_text("body")
                except Exception:  # noqa: BLE001 - 某些页面没有 body 文本
                    text = ""
                status = response.status if response is not None else None
                elapsed = time.monotonic() - started
                final_url = page.url
            except Exception as exc:  # noqa: BLE001 - Playwright 异常类型较多
                last_error = exc
                self.logger.warning("浏览器抓取失败（%d/%d）%s：%s", attempt, self.retries, url, exc)
                page.close()
                self._backoff(attempt)
                continue
            finally:
                self.limiter.mark_request()

            page.close()

            block_reason = detect_block(
                final_url=final_url,
                title=title,
                text=text,
                status=status,
            )
            if block_reason:
                raise BlockedError(url, block_reason, final_url=final_url, status=status)

            if expected_host:
                host = (urlparse(final_url).hostname or "").lower()
                if host != expected_host.lower():
                    raise FetchError(f"跳转到非目标域名 {host}（期望 {expected_host}）：{final_url}")

            if status is not None and status >= 400:
                last_error = FetchError(f"HTTP {status}")
                self.logger.warning("浏览器返回（%d/%d）%s：HTTP %s", attempt, self.retries, url, status)
                self._backoff(attempt)
                continue

            return FetchResult(
                url=url,
                final_url=final_url,
                status=status,
                title=title,
                html=html,
                text=text,
                elapsed=elapsed,
                fetcher="browser",
            )

        raise FetchError(f"重试 {self.retries} 次仍失败：{url}（{last_error}）")

    def _backoff(self, attempt: int) -> None:
        time.sleep(min(2 ** attempt, 10))


class JsonlSink:
    """按 URL 去重的 JSONL 追加写入器。"""

    def __init__(self, path: str | Path, *, dedup_key: str = "url") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.dedup_key = dedup_key
        self.seen: set[str] = set()
        self.written = 0
        self.duplicates = 0
        self._load_existing()
        self._handle = self.path.open("a", encoding="utf-8", newline="\n")

    def _load_existing(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = record.get(self.dedup_key)
                if key:
                    self.seen.add(str(key))

    def write(self, record: dict[str, Any]) -> bool:
        key = record.get(self.dedup_key)
        if key and str(key) in self.seen:
            self.duplicates += 1
            return False
        record.setdefault("crawl_time", now_iso())
        self._handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._handle.flush()
        if key:
            self.seen.add(str(key))
        self.written += 1
        return True

    def write_many(self, records: Iterable[dict[str, Any]]) -> int:
        return sum(1 for record in records if self.write(record))

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "JsonlSink":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


@dataclass(slots=True)
class TargetStats:
    target: str
    pages: int = 0
    rows: int = 0
    written: int = 0
    duplicate: int = 0
    status: str = "ok"  # ok / empty / blocked / error
    note: str = ""


@dataclass
class CrawlStats:
    """单个站点的采集统计，直接用于试点报告。"""

    source: str
    started_at: str = field(default_factory=now_iso)
    finished_at: str | None = None
    pages: int = 0
    rows: int = 0
    written: int = 0
    duplicate: int = 0
    blocked: bool = False
    block_reason: str | None = None
    block_url: str | None = None
    errors: list[str] = field(default_factory=list)
    targets: dict[str, TargetStats] = field(default_factory=dict)

    def target(self, name: str) -> TargetStats:
        if name not in self.targets:
            self.targets[name] = TargetStats(target=name)
        return self.targets[name]

    def add_page(self, target: str, *, rows: int, written: int, duplicate: int, list_url: str) -> None:
        item = self.target(target)
        item.pages += 1
        item.rows += rows
        item.written += written
        item.duplicate += duplicate
        item.note = list_url
        self.pages += 1
        self.rows += rows
        self.written += written
        self.duplicate += duplicate
        if rows == 0 and item.status == "ok":
            item.status = "empty"

    def mark_block(self, target: str, url: str, reason: str) -> None:
        item = self.target(target)
        item.status = "blocked"
        item.note = f"{reason} @ {url}"
        self.blocked = True
        self.block_reason = reason
        self.block_url = url

    def mark_error(self, target: str, message: str) -> None:
        item = self.target(target)
        if item.status == "ok":
            item.status = "error"
        item.note = message
        self.errors.append(message)

    def finish(self) -> None:
        self.finished_at = now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "pages": self.pages,
            "rows": self.rows,
            "written": self.written,
            "duplicate": self.duplicate,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "block_url": self.block_url,
            "errors": list(self.errors),
            "targets": {
                name: {
                    "pages": item.pages,
                    "rows": item.rows,
                    "written": item.written,
                    "duplicate": item.duplicate,
                    "status": item.status,
                    "note": item.note,
                }
                for name, item in self.targets.items()
            },
        }


def setup_logging(
    name: str = "crawler",
    *,
    log_path: str | Path | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """控制台 + 文件日志；重复调用不会叠加 handler。"""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    if log_path is not None:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger
