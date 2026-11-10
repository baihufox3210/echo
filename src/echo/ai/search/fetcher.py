"""網頁抓取：SSRF 防護、逐跳檢查重新導向、大小限制、文件轉文字與連結擷取。"""

import asyncio
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from echo.ai.documents import Link, UnsupportedDocument, extract_text, kind_of, parse_html
from echo.ai.search.common import USER_AGENT, TTLCache, is_public_address
from echo.core.config import Settings

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5
SITEMAP_MAX_URLS = 2000
SITEMAP_MAX_FILES = 4
LOC_PATTERN = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]+)", re.IGNORECASE)


class FetchError(Exception):
    """無法取得頁面；訊息可直接顯示給模型。"""


@dataclass(slots=True)
class FetchedPage:
    url: str
    content_type: str
    title: str
    text: str | None
    links: list[Link] = field(default_factory=list)


class PageFetcher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._cache = TTLCache(settings.search_cache_ttl)
        self._robots = TTLCache(3600.0, max_entries=128)
        # 自行處理重新導向，以便逐跳檢查目標位址
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.search_timeout, connect=10.0),
            follow_redirects=False,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.5",
                "Accept-Language": f"{settings.search_language},en;q=0.7",
            },
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def fetch(self, url: str) -> FetchedPage:
        """下載並轉為純文字（HTML、純文字、PDF、DOCX）；失敗時拋出 FetchError。"""
        url = url.strip()
        if (cached := self._cache.get(url)) is not None:
            return cached

        try:
            final_url, content_type, content, encoding = await self._download(url)
        except httpx.HTTPStatusError as exc:
            raise FetchError(f"HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise FetchError(exc.__class__.__name__) from exc

        try:
            kind = kind_of(urlsplit(final_url).path, content_type)
        except UnsupportedDocument:
            return FetchedPage(final_url, content_type, "", None)
        if kind == "image":
            return FetchedPage(final_url, content_type, "", None)

        try:
            if kind == "html":
                parsed = await asyncio.to_thread(parse_html, content, encoding, final_url)
                page = FetchedPage(final_url, content_type, parsed.title, parsed.text, parsed.links)
            else:
                title, text = await asyncio.to_thread(extract_text, content, kind, encoding)
                page = FetchedPage(final_url, content_type, title, text)
        except UnsupportedDocument as exc:
            raise FetchError(str(exc)) from exc

        self._cache.set(url, page)
        logger.info("Web fetch: url=%s chars=%d links=%d", page.url, len(page.text or ""), len(page.links))
        return page

    async def _download(self, url: str) -> tuple[str, str, bytes, str | None]:
        for _ in range(MAX_REDIRECTS + 1):
            await self._ensure_allowed(url)
            async with self._client.stream("GET", url) as response:
                if response.is_redirect and "location" in response.headers:
                    url = urljoin(str(response.url), response.headers["location"])
                    continue
                response.raise_for_status()
                content = await self._read_limited(response)
                return url, response.headers.get("content-type", "").lower(), content, response.charset_encoding
        raise FetchError("重新導向次數過多")

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
            raise FetchError("僅支援 http/https 網址")
        if self.settings.fetch_allow_private:
            return

        host = parts.hostname
        try:
            addresses = [host] if _is_ip(host) else [
                info[4][0]
                for info in await asyncio.get_running_loop().getaddrinfo(
                    host, parts.port or (443 if parts.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            ]
        except socket.gaierror as exc:
            raise FetchError("無法解析網域") from exc

        if not addresses or not all(is_public_address(a) for a in addresses):
            raise FetchError("不允許存取內部網路位址")

    async def _get_text(self, url: str) -> str | None:
        """抓取 robots.txt、sitemap 等小型文字檔；任何失敗都回傳 None。"""
        try:
            _, _, content, encoding = await self._download(url)
        except (FetchError, httpx.HTTPError):
            return None
        return content.decode(encoding or "utf-8", errors="replace")

    async def _robots_for(self, url: str) -> tuple[RobotFileParser | None, list[str]]:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if (cached := self._robots.get(origin)) is not None:
            return cached

        text = await self._get_text(f"{origin}/robots.txt")
        parser: RobotFileParser | None = None
        sitemaps: list[str] = []
        if text and "<html" not in text[:500].lower():
            parser = RobotFileParser()
            parser.parse(text.splitlines())
            sitemaps = list(parser.site_maps() or [])
        entry = (parser, sitemaps)
        self._robots.set(origin, entry)
        return entry

    async def allowed_by_robots(self, url: str) -> bool:
        """自動探索網站時遵守 robots.txt；讀不到時視為允許。"""
        parser, _ = await self._robots_for(url)
        return parser is None or parser.can_fetch(USER_AGENT, url)

    async def sitemap_urls(self, url: str) -> list[str]:
        """讀取網站的 sitemap（含一層 sitemap index），回傳其中的網址。"""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        _, declared = await self._robots_for(url)
        pending = list(dict.fromkeys([*declared, f"{origin}/sitemap.xml"]))[:SITEMAP_MAX_FILES]

        urls: list[str] = []
        fetched = 0
        while pending and fetched < SITEMAP_MAX_FILES and len(urls) < SITEMAP_MAX_URLS:
            sitemap = pending.pop(0)
            fetched += 1
            text = await self._get_text(sitemap)
            if not text:
                continue
            locations = LOC_PATTERN.findall(text)
            if "<sitemapindex" in text[:2000].lower():
                pending.extend(loc for loc in locations if loc not in pending)
            else:
                urls.extend(locations)
        return list(dict.fromkeys(urls))[:SITEMAP_MAX_URLS]

    async def close(self) -> None:
        await self._client.aclose()


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False
