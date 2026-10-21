import asyncio
import ipaddress
import json
import logging
import re
import socket
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from agents import FunctionTool, RunContextWrapper, function_tool

from echo.ai.context import BUDGET_EXHAUSTED, AgentEvent, RunContext
from echo.ai.documents import UnsupportedDocument, extract_html, extract_text, kind_of, paginate
from echo.core.config import Settings

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; EchoBot/1.0; +https://github.com/baihufox3210/echo)"
MAX_REDIRECTS = 5
SNIPPET_MAX_CHARS = 400
CACHE_MAX_ENTRIES = 256
MAX_PARALLEL_QUERIES = 4
RRF_K = 60

TimeRange = Literal["any", "day", "week", "month", "year"]
Category = Literal["general", "news", "science", "it", "videos"]

__all__ = ["WebSearch", "extract_html", "normalize_url", "is_public_address", "build_query"]


class FetchBlockedError(Exception):
    pass


@dataclass(slots=True)
class FetchedPage:
    url: str
    content_type: str
    title: str
    text: str | None


class TTLCache:
    def __init__(self, ttl: float, max_entries: int = CACHE_MAX_ENTRIES):
        self.ttl = ttl
        self.max_entries = max_entries
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()

    def get(self, key: Any) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        if time.monotonic() - item[0] > self.ttl:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return item[1]

    def set(self, key: Any, value: Any) -> None:
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


def clean_domain(domain: str) -> str:
    domain = domain.strip().lower()
    domain = re.sub(r"^[a-z]+://", "", domain)
    return domain.split("/")[0].removeprefix("www.")


def host_matches(url: str, domain: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return host == domain or host.endswith("." + domain)


def build_query(
    query: str,
    site: str = "",
    exclude_sites: list[str] | None = None,
    exact_phrase: str = "",
    exclude_words: list[str] | None = None,
    filetype: str = "",
) -> str:
    """組合搜尋引擎通用的進階語法。"""
    parts = [query.strip()]
    if exact_phrase.strip():
        parts.append(f'"{exact_phrase.strip().replace(chr(34), "")}"')
    if site.strip():
        parts.append(f"site:{clean_domain(site)}")
    for domain in exclude_sites or []:
        if domain.strip():
            parts.append(f"-site:{clean_domain(domain)}")
    for word in exclude_words or []:
        if word.strip():
            parts.append(f"-{word.strip().replace(' ', '')}")
    if filetype.strip():
        parts.append(f"filetype:{filetype.strip().lstrip('.').lower()}")
    return " ".join(p for p in parts if p)


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
                "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.5",
                "Accept-Language": f"{settings.search_language},en;q=0.7",
            },
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    @property
    def enabled(self) -> bool:
        return self.searxng_url is not None

    # ---------- 搜尋 ----------

    async def search(
        self,
        query: str,
        time_range: TimeRange = "any",
        category: Category = "general",
        language: str = "",
        page: int = 1,
        max_results: int | None = None,
        site: str = "",
        exclude_sites: list[str] | None = None,
    ) -> dict[str, Any]:
        """搜尋並回傳整理過的結果；query 可包含進階語法。"""
        query = query.strip()
        if not query:
            return {"error": "搜尋關鍵字不可為空"}
        if self.searxng_url is None:
            return {"error": "網路搜尋功能未啟用"}

        limit = min(max_results or self.settings.search_max_results, 10)
        page = min(max(page, 1), 5)
        language = language.strip() or self.settings.search_language
        cache_key = ("search", query, time_range, category, language, page, limit, site, tuple(exclude_sites or ()))
        if (cached := self._cache.get(cache_key)) is not None:
            return cached

        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "language": language,
            "categories": category,
            "safesearch": 1,
            "pageno": page,
        }
        if time_range != "any":
            params["time_range"] = time_range

        try:
            data = await self._query_searxng(params)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Web search failed: %s", exc.__class__.__name__)
            return {"error": f"搜尋失敗：{exc.__class__.__name__}"}

        payload = self._format_results(query, data, limit, site=site, exclude_sites=exclude_sites)
        if payload["results"] or payload.get("answers"):
            self._cache.set(cache_key, payload)

        logger.info("Web search: query=%r results=%d", query, len(payload["results"]))
        return payload

    async def multi_search(
        self,
        queries: list[str],
        time_range: TimeRange = "any",
        category: Category = "general",
    ) -> dict[str, Any]:
        """並行執行多個查詢，以 Reciprocal Rank Fusion 合併排序。"""
        queries = list(dict.fromkeys(q.strip() for q in queries if q.strip()))[:MAX_PARALLEL_QUERIES]
        if not queries:
            return {"error": "至少需要一個查詢"}

        payloads = await asyncio.gather(*(
            self.search(q, time_range=time_range, category=category, max_results=8) for q in queries
        ))

        scores: dict[str, float] = {}
        entries: dict[str, dict[str, Any]] = {}
        answers: list[str] = []
        for payload in payloads:
            answers.extend(payload.get("answers", []))
            for rank, entry in enumerate(payload.get("results", [])):
                key = normalize_url(entry["url"])
                scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
                entries.setdefault(key, {**entry, "matched_queries": []})["matched_queries"].append(payload["query"])

        ranked = sorted(entries, key=lambda k: scores[k], reverse=True)
        results = [entries[k] for k in ranked[: self.settings.search_max_results + 3]]

        merged: dict[str, Any] = {"queries": queries, "results": results}
        if answers:
            merged["answers"] = list(dict.fromkeys(answers))[:3]
        errors = [p["error"] for p in payloads if "error" in p]
        if errors and not results:
            merged["error"] = errors[0]
        return merged

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
    def _format_results(
        query: str,
        data: dict[str, Any],
        limit: int,
        site: str = "",
        exclude_sites: list[str] | None = None,
    ) -> dict[str, Any]:
        site = clean_domain(site) if site else ""
        excluded = [clean_domain(d) for d in exclude_sites or [] if d.strip()]
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        filtered_out = 0

        for item in data.get("results", []):
            url = item.get("url") or ""
            if not url.startswith(("http://", "https://")):
                continue
            key = normalize_url(url)
            if key in seen:
                continue
            seen.add(key)

            # 部分搜尋引擎不支援 site: 語法，於此再過濾一次
            if (site and not host_matches(url, site)) or any(host_matches(url, d) for d in excluded):
                filtered_out += 1
                continue

            entry = {
                "title": truncate(item.get("title", ""), 200),
                "url": url,
                "snippet": truncate(item.get("content", ""), SNIPPET_MAX_CHARS),
            }
            if item.get("publishedDate"):
                entry["published"] = str(item["publishedDate"])[:10]
            if item.get("engines"):
                entry["engines"] = len(item["engines"])
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
        if not results and data.get("corrections"):
            payload["corrections"] = list(data["corrections"])[:3]
        if not results and filtered_out:
            payload["note"] = f"有 {filtered_out} 筆結果因網域條件被排除，可放寬 site 條件再試。"
        elif not results and data.get("unresponsive_engines"):
            payload["note"] = "部分搜尋引擎暫時無回應，可稍後重試或更換關鍵字。"

        return payload

    # ---------- 擷取網頁 ----------

    async def fetch(self, url: str) -> FetchedPage:
        """下載並轉為純文字（支援 HTML、純文字、PDF、DOCX）；結果會快取。"""
        url = url.strip()
        cache_key = ("fetch", url)
        if (cached := self._cache.get(cache_key)) is not None:
            return cached

        page = await self._fetch(url)
        if page.text is not None:
            self._cache.set(cache_key, page)
            logger.info("Web fetch: url=%s chars=%d", page.url, len(page.text))
        return page

    async def _fetch(self, url: str) -> FetchedPage:
        for _ in range(MAX_REDIRECTS + 1):
            await self._ensure_allowed(url)

            async with self._fetch_client.stream("GET", url) as response:
                if response.is_redirect and "location" in response.headers:
                    url = urljoin(str(response.url), response.headers["location"])
                    continue

                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                try:
                    kind = kind_of(urlsplit(url).path, content_type)
                except UnsupportedDocument:
                    return FetchedPage(url, content_type, "", None)
                if kind == "image":
                    return FetchedPage(url, content_type, "", None)

                content = await self._read_limited(response)
                encoding = response.charset_encoding

            try:
                title, text = await asyncio.to_thread(extract_text, content, kind, encoding)
            except UnsupportedDocument as exc:
                raise FetchBlockedError(str(exc)) from exc
            return FetchedPage(url, content_type, title, text)

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

    async def read_page(self, url: str, start: int = 0) -> str:
        """供工具使用：讀取網頁並依 start 分頁回傳。"""
        try:
            page = await self.fetch(url)
        except FetchBlockedError as exc:
            return f"讀取網頁失敗：{exc}"
        except httpx.HTTPStatusError as exc:
            return f"讀取網頁失敗：HTTP {exc.response.status_code}"
        except httpx.HTTPError as exc:
            return f"讀取網頁失敗：{exc.__class__.__name__}"

        if page.text is None:
            return f"URL: {page.url}\nContent-Type: {page.content_type}\n此資源不是可讀取的文字或文件。"

        chunk, next_start = paginate(page.text, start, self.settings.fetch_max_chars)
        header = [f"URL: {page.url}"]
        if page.title:
            header.append(f"標題: {page.title}")
        header.append(f"範圍: 第 {start} 字起，共 {len(page.text)} 字")
        if next_start is not None:
            header.append(f"尚有後續內容，可用 web_fetch(url, start={next_start}) 繼續讀取")
        return "\n".join(header) + "\n\n" + chunk

    # ---------- 工具 ----------

    def build_tools(self) -> list[FunctionTool]:
        @function_tool
        async def web_search(
            ctx: RunContextWrapper[RunContext],
            query: str,
            time_range: TimeRange = "any",
            category: Category = "general",
            site: str = "",
            exclude_sites: list[str] | None = None,
            exact_phrase: str = "",
            exclude_words: list[str] | None = None,
            filetype: str = "",
            language: str = "",
            page: int = 1,
        ) -> str:
            """搜尋網際網路，回傳標題、網址、摘要與發布日期。支援進階搜尋條件。

            Args:
                query: 搜尋關鍵字。使用精簡、具體的關鍵字，必要時加入年份或地區。
                time_range: 時間範圍。新聞或近期事件用 day、week 或 month；否則用 any。
                category: 類別。新聞時事用 news；科技用 it；學術用 science；影片用 videos；其他用 general。
                site: 只搜尋特定網站，例如 wikipedia.org；不限制則留空。
                exclude_sites: 要排除的網站網域列表。
                exact_phrase: 必須完整出現的詞組；不需要則留空。
                exclude_words: 結果中不可出現的詞。
                filetype: 限定檔案類型，例如 pdf；不限制則留空。
                language: 搜尋語言代碼，例如 en、ja、zh-TW；留空使用預設語言。查詢國外資訊時可改用 en。
                page: 結果頁數 1~5，想看更多結果時遞增。
            """
            context = ctx.context
            if not context.consume_budget():
                return BUDGET_EXHAUSTED

            full_query = build_query(query, site, exclude_sites, exact_phrase, exclude_words, filetype)
            await context.emit(AgentEvent("tool", f"🔍 搜尋「{truncate(full_query, 60)}」"))
            payload = await self.search(
                full_query, time_range=time_range, category=category, language=language,
                page=page, site=site, exclude_sites=exclude_sites,
            )
            return self._wrap_results(context, "web_search", payload)

        @function_tool
        async def multi_search(
            ctx: RunContextWrapper[RunContext],
            queries: list[str],
            time_range: TimeRange = "any",
            category: Category = "general",
        ) -> str:
            """同時以多個不同關鍵字搜尋並合併排序結果，適合需要多角度查證或中英文並查的問題。

            Args:
                queries: 2~4 個不同角度或語言的搜尋關鍵字。
                time_range: 時間範圍，用法同 web_search。
                category: 類別，用法同 web_search。
            """
            context = ctx.context
            if not context.consume_budget():
                return BUDGET_EXHAUSTED

            await context.emit(AgentEvent("tool", f"🔍 同時搜尋 {len(queries)} 組關鍵字"))
            payload = await self.multi_search(queries, time_range=time_range, category=category)
            return self._wrap_results(context, "multi_search", payload)

        @function_tool
        async def web_fetch(ctx: RunContextWrapper[RunContext], url: str, start: int = 0) -> str:
            """讀取網頁或線上文件（HTML、PDF、DOCX、純文字）的內容。搜尋摘要不足時使用。

            Args:
                url: 完整的 http 或 https 網址，通常取自搜尋結果。
                start: 從第幾個字開始讀取；長文件可依回傳的提示分段讀取，第一次使用 0。
            """
            context = ctx.context
            if not context.consume_budget():
                return BUDGET_EXHAUSTED

            await context.emit(AgentEvent("tool", f"📄 閱讀 {truncate(urlsplit(url).netloc or url, 60)}"))
            result = await self.read_page(url, start=max(0, start))
            if not result.startswith("讀取網頁失敗"):
                context.seen_urls.add(url)
                if match := re.match(r"URL: (\S+)", result):
                    context.seen_urls.add(match.group(1))
            return context.guard.wrap(f"web_fetch:{url}", result)

        tools = [web_fetch]
        if self.enabled:
            tools = [web_search, multi_search, web_fetch]
        return tools

    @staticmethod
    def _wrap_results(context: RunContext, source: str, payload: dict[str, Any]) -> str:
        for entry in payload.get("results", []):
            context.seen_urls.add(entry["url"])
        return context.guard.wrap(source, json.dumps(payload, ensure_ascii=False))

    async def close(self) -> None:
        await self._search_client.aclose()
        await self._fetch_client.aclose()
