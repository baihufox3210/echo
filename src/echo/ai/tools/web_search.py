import asyncio
import ipaddress
import json
import logging
import re
import socket
import time
from collections import OrderedDict
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from agents import FunctionTool, function_tool
from bs4 import BeautifulSoup

from echo.core.config import Settings

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; EchoBot/1.0; +https://github.com/baihufox3210/echo)"
MAX_REDIRECTS = 5
SNIPPET_MAX_CHARS = 400
CACHE_MAX_ENTRIES = 256

REMOVED_TAGS = (
    "script", "style", "noscript", "svg", "nav", "footer", "header",
    "form", "aside", "iframe", "button", "template",
)
TEXT_CONTENT_TYPES = ("text/plain", "text/markdown", "application/json", "text/csv")
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

TimeRange = Literal["any", "day", "week", "month", "year"]
Category = Literal["general", "news", "science", "it"]


class FetchBlockedError(Exception):
    pass


class TTLCache:
    def __init__(self, ttl: float, max_entries: int = CACHE_MAX_ENTRIES):
        self.ttl = ttl
        self.max_entries = max_entries
        self._data: OrderedDict[Any, tuple[float, str]] = OrderedDict()

    def get(self, key: Any) -> str | None:
        item = self._data.get(key)
        if item is None:
            return None
        if time.monotonic() - item[0] > self.ttl:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return item[1]

    def set(self, key: Any, value: str) -> None:
        if self.ttl <= 0:
            return
        self._data[key] = (time.monotonic(), value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)


def normalize_url(url: str) -> str:
    """去除 fragment、追蹤參數與結尾斜線，用於結果去重。"""
    parts = urlsplit(url)
    query = "&".join(
        p for p in parts.query.split("&")
        if p and not p.lower().startswith(("utm_", "fbclid=", "gclid="))
    )
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


def truncate(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def extract_html(content: bytes, encoding: str | None, max_chars: int) -> tuple[str, str]:
    """從 HTML 擷取標題與主要文字內容。"""
    soup = BeautifulSoup(content, "html.parser", from_encoding=encoding)

    title = soup.title.get_text(strip=True) if soup.title else ""
    description = ""
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    if meta and meta.get("content"):
        description = str(meta["content"]).strip()

    for element in soup(REMOVED_TAGS):
        element.decompose()

    root = (
        soup.find("article")
        or soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.body
        or soup
    )

    lines: list[str] = []
    seen: set[str] = set()
    for line in root.get_text(separator="\n").splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)

    text = "\n".join(lines)
    if description and description not in text:
        text = f"{description}\n\n{text}"
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n...[內容已截斷]"

    return title, text


class WebSearch:
    """SearXNG 搜尋與網頁擷取，提供給 AI 代理使用的工具。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.searxng_url = settings.searxng_url.rstrip("/") if settings.searxng_url else None
        self._cache = TTLCache(settings.search_cache_ttl)

        self._search_client = httpx.AsyncClient(
            timeout=settings.search_timeout,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        # 擷取網頁時自行處理重新導向，以便逐跳檢查目標位址
        self._fetch_client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.search_timeout, connect=10.0),
            follow_redirects=False,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
                "Accept-Language": f"{settings.search_language},en;q=0.7",
            },
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    @property
    def enabled(self) -> bool:
        return self.searxng_url is not None

    async def search(
        self,
        query: str,
        time_range: TimeRange = "any",
        category: Category = "general",
        max_results: int | None = None,
    ) -> str:
        query = query.strip()
        if not query:
            return json.dumps({"error": "搜尋關鍵字不可為空"}, ensure_ascii=False)
        if self.searxng_url is None:
            return json.dumps({"error": "網路搜尋功能未啟用"}, ensure_ascii=False)

        limit = min(max_results or self.settings.search_max_results, 10)
        cache_key = ("search", query, time_range, category, limit)
        if cached := self._cache.get(cache_key):
            return cached

        params = {
            "q": query,
            "format": "json",
            "language": self.settings.search_language,
            "categories": category,
            "safesearch": 1,
        }
        if time_range != "any":
            params["time_range"] = time_range

        try:
            data = await self._query_searxng(params)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Web search failed: %s", exc.__class__.__name__)
            return json.dumps({"error": f"搜尋失敗：{exc.__class__.__name__}"}, ensure_ascii=False)

        payload = self._format_results(query, data, limit)
        result = json.dumps(payload, ensure_ascii=False)
        if payload["results"] or payload.get("answers"):
            self._cache.set(cache_key, result)

        logger.info("Web search: query=%r results=%d", query, len(payload["results"]))
        return result

    async def _query_searxng(self, params: dict[str, Any]) -> dict[str, Any]:
        """查詢 SearXNG；遇到連線錯誤、429 或 5xx 時重試一次。"""
        attempts = 2
        for attempt in range(attempts):
            try:
                response = await self._search_client.get(f"{self.searxng_url}/search", params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if attempt == attempts - 1 or (status < 500 and status != 429):
                    raise
            except httpx.TransportError:
                if attempt == attempts - 1:
                    raise
            await asyncio.sleep(0.5)
        raise AssertionError("unreachable")

    @staticmethod
    def _format_results(query: str, data: dict[str, Any], limit: int) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        seen: set[str] = set()

        for item in data.get("results", []):
            url = item.get("url") or ""
            if not url.startswith(("http://", "https://")):
                continue
            key = normalize_url(url)
            if key in seen:
                continue
            seen.add(key)

            entry = {
                "title": truncate(item.get("title", ""), 200),
                "url": url,
                "snippet": truncate(item.get("content", ""), SNIPPET_MAX_CHARS),
            }
            if item.get("publishedDate"):
                entry["published"] = str(item["publishedDate"])[:10]
            results.append(entry)

            if len(results) >= limit:
                break

        payload: dict[str, Any] = {"query": query, "results": results}

        answers = [
            truncate(a.get("answer", "") if isinstance(a, dict) else str(a), SNIPPET_MAX_CHARS)
            for a in data.get("answers", [])
        ]
        if answers := [a for a in answers if a]:
            payload["answers"] = answers[:3]

        infoboxes = [
            {"title": box.get("infobox", ""), "content": truncate(box.get("content", ""), 800)}
            for box in data.get("infoboxes", [])
            if box.get("content")
        ]
        if infoboxes:
            payload["infobox"] = infoboxes[0]

        if not results and data.get("suggestions"):
            payload["suggestions"] = list(data["suggestions"])[:5]

        if not results and data.get("unresponsive_engines"):
            payload["note"] = "部分搜尋引擎暫時無回應，可稍後重試或更換關鍵字。"

        return payload

    async def fetch(self, url: str) -> str:
        url = url.strip()
        cache_key = ("fetch", url)
        if cached := self._cache.get(cache_key):
            return cached

        try:
            final_url, content_type, title, text = await self._fetch(url)
        except FetchBlockedError as exc:
            return f"讀取網頁失敗：{exc}"
        except httpx.HTTPStatusError as exc:
            return f"讀取網頁失敗：HTTP {exc.response.status_code}"
        except httpx.HTTPError as exc:
            return f"讀取網頁失敗：{exc.__class__.__name__}"

        if text is None:
            return f"URL: {final_url}\nContent-Type: {content_type}\n此資源不是可讀取的文字網頁。"

        header = f"URL: {final_url}"
        if title:
            header += f"\n標題: {title}"
        result = f"{header}\n\n{text}"
        self._cache.set(cache_key, result)

        logger.info("Web fetch: url=%s chars=%d", final_url, len(text))
        return result

    async def _fetch(self, url: str) -> tuple[str, str, str, str | None]:
        for _ in range(MAX_REDIRECTS + 1):
            await self._ensure_allowed(url)

            async with self._fetch_client.stream("GET", url) as response:
                if response.is_redirect and "location" in response.headers:
                    url = urljoin(str(response.url), response.headers["location"])
                    continue

                response.raise_for_status()

                content_type = response.headers.get("content-type", "").lower()
                mime = content_type.split(";")[0].strip()
                if mime not in HTML_CONTENT_TYPES and mime not in TEXT_CONTENT_TYPES:
                    return url, content_type, "", None

                content = await self._read_limited(response)

            max_chars = self.settings.fetch_max_chars
            if mime in HTML_CONTENT_TYPES:
                title, text = await asyncio.to_thread(
                    extract_html, content, response.charset_encoding, max_chars
                )
                return url, content_type, title, text

            text = content.decode(response.charset_encoding or "utf-8", errors="replace")
            if len(text) > max_chars:
                text = text[:max_chars] + "\n...[內容已截斷]"
            return url, content_type, "", text

        raise FetchBlockedError("重新導向次數過多")

    async def _read_limited(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= self.settings.fetch_max_bytes:
                break
        return b"".join(chunks)[: self.settings.fetch_max_bytes]

    async def _ensure_allowed(self, url: str) -> None:
        """阻擋非 HTTP(S) 及指向內網、本機的位址，避免 SSRF。"""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchBlockedError("僅支援 http/https 網址")
        if self.settings.fetch_allow_private:
            return

        host = parts.hostname
        try:
            addresses = [host] if self._is_ip(host) else [
                info[4][0]
                for info in await asyncio.get_running_loop().getaddrinfo(
                    host, parts.port or (443 if parts.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            ]
        except socket.gaierror as exc:
            raise FetchBlockedError("無法解析網域") from exc

        if not addresses or not all(is_public_address(a) for a in addresses):
            raise FetchBlockedError("不允許存取內部網路位址")

    @staticmethod
    def _is_ip(host: str) -> bool:
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            return False

    def build_tools(self) -> list[FunctionTool]:
        @function_tool
        async def web_search(
            query: str,
            time_range: TimeRange = "any",
            category: Category = "general",
        ) -> str:
            """搜尋網際網路上的最新資訊，回傳標題、網址、摘要與發布日期。

            Args:
                query: 搜尋關鍵字。使用精簡、具體的關鍵字，必要時加入年份或地區。
                time_range: 限制結果時間範圍。查詢新聞或近期事件時使用 day、week 或 month；否則使用 any。
                category: 搜尋類別。新聞與時事使用 news；科技問題使用 it；學術研究使用 science；其他使用 general。
            """
            return await self.search(query, time_range=time_range, category=category)

        @function_tool
        async def web_fetch(url: str) -> str:
            """讀取指定網址的網頁正文。當搜尋摘要不足以回答問題時使用。

            Args:
                url: 完整的 http 或 https 網址，通常取自 web_search 的結果。
            """
            return await self.fetch(url)

        return [web_search, web_fetch] if self.enabled else [web_fetch]

    async def close(self) -> None:
        await self._search_client.aclose()
        await self._fetch_client.aclose()
