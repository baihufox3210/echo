"""站內查找：在單一網站內找出與問題相關的頁面並擷取段落。

很多資訊（課程地圖、師資、修業規定、招生簡章）不會出現在搜尋引擎的前幾筆結果，
而是藏在系所網站的選單或附件裡。這裡綜合三種線索找出候選頁面：
  1. 起始頁面上的連結（包含導覽選單）
  2. 網站的 sitemap
  3. 搜尋引擎的站內搜尋（site:）
依連結文字／網址與問題的相關度排序後，實際讀取最相關的幾頁（最多兩層），
再從每頁取出與問題最相關的段落。
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from echo.ai.documents import Link
from echo.ai.search.common import hostname, normalize_url, same_site, truncate
from echo.ai.search.engine import SearchEngine
from echo.ai.search.fetcher import FetchedPage, FetchError, PageFetcher
from echo.ai.search.text import best_passages, query_terms, relevance
from echo.core.config import Settings

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES = 6
HARD_MAX_PAGES = 10
FIRST_ROUND = 4
CONCURRENCY = 3
MIN_PASSAGE_CHARS = 1200
SKIPPED_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".css", ".js", ".zip", ".rar", ".7z",
    ".mp3", ".mp4", ".avi", ".mov", ".ppt", ".pptx", ".xls", ".xlsx", ".exe", ".woff", ".woff2",
)
SKIPPED_PATH_WORDS = ("login", "logout", "signin", "signup", "register", "wp-admin", "javascript:")
# 想找的細節與網站欄目名稱常常字面不同（找「必修科目」要先進「課程資訊」）。
# 查詢包含左邊任一詞時，也把右邊的欄目用詞當作較弱的線索，用來挑選要進入的欄目頁。
SECTION_HINTS = (
    (("必修", "選修", "學分", "科目", "課表", "修課", "課綱", "課程", "學程", "curriculum", "course"),
     ("課程", "修業", "學程", "課表", "地圖", "course", "curriculum")),
    (("教授", "老師", "教師", "師資", "專長", "faculty", "professor"),
     ("師資", "教師", "成員", "faculty", "people")),
    (("招生", "報名", "簡章", "名額", "入學", "甄試", "申請入學", "admission"),
     ("招生", "入學", "admission")),
    (("畢業", "門檻", "規定", "辦法", "法規", "修業", "規則"),
     ("修業", "法規", "規章", "辦法", "畢業", "表單", "下載")),
    (("獎學金", "補助", "學費", "scholarship"), ("獎助", "獎學金", "學務", "scholarship")),
    (("公告", "消息", "活動", "演講", "news"), ("公告", "消息", "活動", "news")),
    (("電話", "地址", "聯絡", "信箱", "位置", "contact"), ("聯絡", "關於", "簡介", "contact", "about")),
    (("簡介", "特色", "目標", "介紹", "沿革", "about"), ("簡介", "關於", "介紹", "特色", "about")),
)
HINT_WEIGHT = 0.6


@dataclass(slots=True)
class Candidate:
    url: str
    text: str
    score: float
    source: str


def url_words(url: str) -> str:
    """把網址路徑轉成可比對的文字（解碼中文、分隔符號轉空白）。"""
    parts = urlsplit(url)
    path = unquote(f"{parts.path} {parts.query}")
    for char in "/-_=&.+":
        path = path.replace(char, " ")
    return path


def section_hints(query: str) -> list[str]:
    lowered = query.lower()
    hints: list[str] = []
    for triggers, sections in SECTION_HINTS:
        if any(trigger in lowered for trigger in triggers):
            hints.extend(word for word in sections if word not in hints)
    return hints


def crawlable(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return not path.endswith(SKIPPED_EXTENSIONS) and not any(word in url.lower() for word in SKIPPED_PATH_WORDS)


class SiteExplorer:
    def __init__(self, fetcher: PageFetcher, engine: SearchEngine, settings: Settings):
        self.fetcher = fetcher
        self.engine = engine
        self.settings = settings

    async def explore(self, url: str, query: str, max_pages: int = DEFAULT_MAX_PAGES) -> dict[str, Any]:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        terms = query_terms(query)
        if not terms:
            return {"error": "請提供要在網站中尋找的內容"}
        max_pages = min(max(max_pages, 1), HARD_MAX_PAGES)
        hints = section_hints(query)

        try:
            start = await self.fetcher.fetch(url)
        except FetchError as exc:
            return {"error": f"無法開啟網站：{exc}", "site": url}

        base = start.url
        prefix = self._path_prefix(base)
        candidates: dict[str, Candidate] = {}

        def add(link_url: str, text: str, source: str, bonus: float = 0.0) -> None:
            key = normalize_url(link_url)
            if key == normalize_url(base) or not same_site(link_url, base) or not crawlable(link_url):
                return
            words = f"{text} {url_words(link_url)}"
            score = relevance(terms, words) + bonus
            if hints and any(hint in words.lower() for hint in hints):
                score += HINT_WEIGHT
            # 同一個子目錄（例如系所自己的路徑）下的頁面略微優先
            if prefix and urlsplit(link_url).path.startswith(prefix):
                score += 0.3
            existing = candidates.get(key)
            if existing is None or score > existing.score:
                candidates[key] = Candidate(link_url, text or (existing.text if existing else ""), score, source)

        for link in start.links:
            add(link.url, link.text, "menu")

        sitemap, site_search = await asyncio.gather(
            self.fetcher.sitemap_urls(base),
            self._site_search(base, query),
        )
        for sitemap_url in sitemap:
            add(sitemap_url, "", "sitemap")
        for entry in site_search:
            # 搜尋引擎認為相關的頁面額外加分
            add(entry["url"], f"{entry['title']} {entry['snippet']}", "search", bonus=1.5)

        pages: dict[str, FetchedPage] = {normalize_url(base): start}
        tried: set[str] = set(pages)

        first = self._top(candidates, tried, FIRST_ROUND if max_pages > 1 else 0)
        await self._fetch_all(first, pages, tried)

        # 第二層：從剛讀到的頁面再找更相關的連結（例如「課程資訊 → 課程地圖.pdf」）
        for page in list(pages.values()):
            if page is start:
                continue
            for link in page.links:
                add(link.url, link.text, "link")
        remaining = max_pages - (len(pages) - 1)
        if remaining > 0:
            best = max((c.score for c in candidates.values()), default=0.0)
            second = [c for c in self._top(candidates, tried, remaining) if c.score >= max(1.0, best * 0.4)]
            await self._fetch_all(second, pages, tried)

        return self._report(base, query, terms, start, pages, candidates, tried)

    @staticmethod
    def _path_prefix(url: str) -> str:
        """起始網址若位於子目錄（如 /dept/ais/），回傳該目錄作為範圍提示。"""
        path = urlsplit(url).path
        directory = path[: path.rfind("/") + 1]
        return directory if directory.count("/") >= 2 else ""

    async def _site_search(self, base: str, query: str) -> list[dict[str, Any]]:
        if not self.engine.enabled:
            return []
        host = hostname(base).removeprefix("www.")
        payload = await self.engine.search(f"{query} site:{host}", site=host, max_results=10)
        return payload.get("results", [])

    @staticmethod
    def _top(candidates: dict[str, Candidate], tried: set[str], count: int) -> list[Candidate]:
        ranked = sorted(
            (c for key, c in candidates.items() if key not in tried and c.score > 0),
            key=lambda c: c.score,
            reverse=True,
        )
        return ranked[:count]

    async def _fetch_all(self, chosen: list[Candidate], pages: dict[str, FetchedPage], tried: set[str]) -> None:
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def fetch(candidate: Candidate) -> None:
            key = normalize_url(candidate.url)
            tried.add(key)
            async with semaphore:
                if not await self.fetcher.allowed_by_robots(candidate.url):
                    logger.info("Skipping %s (disallowed by robots.txt)", candidate.url)
                    return
                try:
                    page = await self.fetcher.fetch(candidate.url)
                except FetchError as exc:
                    logger.info("Site explore: failed to fetch %s (%s)", candidate.url, exc)
                    return
            if page.text:
                pages[key] = page

        await asyncio.gather(*(fetch(c) for c in chosen))

    def _report(
        self,
        base: str,
        query: str,
        terms: list[str],
        start: FetchedPage,
        pages: dict[str, FetchedPage],
        candidates: dict[str, Candidate],
        tried: set[str],
    ) -> dict[str, Any]:
        scored = []
        for page in pages.values():
            score = self._page_score(terms, page)
            if score > 0:
                scored.append((score, page))
        scored.sort(key=lambda item: item[0], reverse=True)

        budget = self.settings.fetch_max_chars
        per_page = max(MIN_PASSAGE_CHARS, budget // max(len(scored), 1))
        pages_read = []
        for _, page in scored:
            if budget <= 0:
                break
            passages = best_passages(page.text or "", terms, min(per_page, budget))
            if not passages:
                continue
            budget -= len(passages)
            pages_read.append({"url": page.url, "title": truncate(page.title, 120), "passages": passages})

        other = sorted(
            (c for key, c in candidates.items() if key not in tried and c.score > 0),
            key=lambda c: c.score,
            reverse=True,
        )[:10]

        report: dict[str, Any] = {
            "site": hostname(base),
            "query": query,
            "pages_checked": len(pages),
            "pages_read": pages_read,
        }
        if other:
            report["other_relevant_links"] = [{"url": c.url, "text": truncate(c.text, 80)} for c in other]

        if not pages_read:
            # 找不到相關內容時，回報網站選單，讓模型自行判斷該看哪一頁或換個說法
            report["site_menu"] = self._menu(start.links, base)
            report["note"] = (
                "站內沒有找到與查詢直接相符的內容。可參考 site_menu 的頁面名稱，"
                "改用網站上實際使用的詞彙再查一次，或用 web_fetch 直接閱讀其中的頁面。"
                "若網站內容由 JavaScript 動態載入，則無法讀取。"
            )
        return report

    @staticmethod
    def _page_score(terms: list[str], page: FetchedPage) -> float:
        """頁面本身內容的相關度。

        只是「連結文字」符合的目錄頁要排在真正含有內容的頁面後面，
        因此先以扣除連結文字後的內文計分，內文完全不符時才以較低權重採計整頁。
        """
        text = (page.text or "")[:30_000]
        link_texts = {link.text for link in page.links if link.text}
        body = "\n".join(line for line in text.splitlines() if line not in link_texts)
        own = relevance(terms, f"{page.title} {body}")
        return own if own > 0 else 0.5 * relevance(terms, text)

    @staticmethod
    def _menu(links: list[Link], base: str, limit: int = 30) -> list[dict[str, str]]:
        menu: list[dict[str, str]] = []
        seen: set[str] = set()
        for link in links:
            if not link.text or link.text in seen or not same_site(link.url, base) or not crawlable(link.url):
                continue
            seen.add(link.text)
            menu.append({"text": truncate(link.text, 60), "url": link.url})
            if len(menu) >= limit:
                break
        return menu
