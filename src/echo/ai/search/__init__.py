"""聯網搜尋。

採用分階段的查詢流程，對應到四個工具：
  1. discover      模糊探索：使用者的說法常是簡稱或不精確，先找出正式名稱與官方網站
  2. web_search    精確搜尋：用正式名稱加上想查的細節，可同時送出多組關鍵字
  3. explore_site  站內查找：在官方網站內沿著選單、sitemap 與站內搜尋找出藏在裡面的頁面
  4. web_fetch     閱讀：讀取單一頁面或文件，可指定關鍵字只取相關段落
"""

import json
from typing import Any
from urllib.parse import urlsplit

from agents import FunctionTool, RunContextWrapper, function_tool

from echo.ai.context import BUDGET_EXHAUSTED, AgentEvent, RunContext
from echo.ai.documents import paginate
from echo.ai.search.common import normalize_url, same_site, truncate
from echo.ai.search.engine import Category, SearchEngine, TimeRange, build_query
from echo.ai.search.fetcher import FetchError, PageFetcher
from echo.ai.search.site import DEFAULT_MAX_PAGES, SiteExplorer, crawlable
from echo.ai.search.text import best_passages, query_terms, relevance
from echo.core.config import Settings

__all__ = ["WebSearch", "normalize_url"]

HUB_PAGE_CHARS = 600
MAX_PAGE_LINKS = 20


class WebSearch:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = SearchEngine(settings)
        self.fetcher = PageFetcher(settings)
        self.explorer = SiteExplorer(self.fetcher, self.engine, settings)

    @property
    def enabled(self) -> bool:
        return self.engine.enabled

    async def read_page(self, url: str, start: int = 0, find: str = "") -> str:
        """讀取網頁；find 有值時只回傳相關段落，否則依 start 分頁。"""
        try:
            page = await self.fetcher.fetch(url)
        except FetchError as exc:
            return f"讀取網頁失敗：{exc}"

        if page.text is None:
            return f"URL: {page.url}\nContent-Type: {page.content_type}\n此資源不是可讀取的文字或文件。"

        header = [f"URL: {page.url}"]
        if page.title:
            header.append(f"標題: {page.title}")
        terms = query_terms(find)

        if terms:
            body = best_passages(page.text, terms, self.settings.fetch_max_chars)
            header.append(f"全文共 {len(page.text)} 字，以下為與「{truncate(find, 40)}」相關的段落")
            if not body:
                body = "（頁面中找不到相關段落；可改用其他關鍵字，或不指定 find 直接閱讀全文。）"
        else:
            body, next_start = paginate(page.text, start, self.settings.fetch_max_chars)
            header.append(f"範圍: 第 {start} 字起，共 {len(page.text)} 字")
            if next_start is not None:
                header.append(f"尚有後續內容，可用 web_fetch(url, start={next_start}) 繼續讀取")

        links = self._page_links(page, terms)
        result = "\n".join(header) + "\n\n" + body
        if links:
            result += "\n\n頁面上的相關連結：\n" + "\n".join(f"- {text}：{link}" for text, link in links)
        return result

    @staticmethod
    def _page_links(page, terms: list[str]) -> list[tuple[str, str]]:
        """列出值得繼續點的站內連結：有關鍵字時取相關者；內容很少的目錄頁則列出選單。"""
        internal = [l for l in page.links if l.text and same_site(l.url, page.url) and crawlable(l.url)]
        if terms:
            scored = sorted(((relevance(terms, l.text), l) for l in internal), key=lambda x: x[0], reverse=True)
            chosen = [l for score, l in scored if score > 0][:8]
        elif len(page.text or "") < HUB_PAGE_CHARS:
            chosen = internal[:MAX_PAGE_LINKS]
        else:
            chosen = []
        return [(truncate(l.text, 60), l.url) for l in chosen]

    def build_tools(self) -> list[FunctionTool]:
        @function_tool
        async def discover(ctx: RunContextWrapper[RunContext], topic: str) -> str:
            """查資料的第一步：模糊探索一個主題，找出它的正式名稱、官方網站與相關條目。
            對方的說法是簡稱、暱稱、不確定全名，或你不確定它指的是什麼時，先用這個工具。
            例如「輔大智慧資安」會找到正式名稱「人工智慧與資訊安全學士學位學程」及其官方網站。

            Args:
                topic: 對方原本的說法或主題關鍵字，保持簡短，不要加上想查的細節。
            """
            context = ctx.context
            if not context.consume_budget():
                return BUDGET_EXHAUSTED

            await context.emit(AgentEvent("tool", f"▹ 探索「{truncate(topic, 40)}」是什麼"))
            payload = await self.engine.discover(topic)
            return self._wrap(context, "discover", payload)

        @function_tool
        async def web_search(
            ctx: RunContextWrapper[RunContext],
            queries: list[str],
            site: str = "",
            time_range: TimeRange = "any",
            category: Category = "general",
            exact_phrase: str = "",
            filetype: str = "",
            language: str = "",
            page: int = 1,
        ) -> str:
            """精確搜尋網際網路，回傳標題、網址、摘要與發布日期。
            關鍵字要具體：使用正式名稱（不確定時先用 discover）加上想查的細節。
            可一次給多組關鍵字（不同說法、中英文），結果會合併排序。

            Args:
                queries: 1～4 組搜尋關鍵字，例如 ["輔仁大學 人工智慧與資訊安全學士學位學程 必修課程"]。
                site: 只搜尋特定網站，例如 fju.edu.tw；不限制則留空。
                time_range: 時間範圍。新聞或近期事件用 day、week 或 month；否則用 any。
                category: 類別。新聞時事用 news；科技用 it；學術用 science；影片用 videos；其他用 general。
                exact_phrase: 必須完整出現的詞組；不需要則留空。
                filetype: 限定檔案類型，例如 pdf；不限制則留空。
                language: 搜尋語言代碼，例如 en、ja；留空使用預設語言。
                page: 結果頁數 1～5，想看更多結果時遞增。
            """
            context = ctx.context
            if not context.consume_budget():
                return BUDGET_EXHAUSTED

            full = [build_query(q, site, exact_phrase, filetype) for q in queries if q.strip()]
            await context.emit(AgentEvent("tool", f"▹ 搜尋「{truncate('、'.join(queries), 60)}」"))
            payload = await self.engine.search_many(
                full, time_range=time_range, category=category, language=language, page=page, site=site,
            )
            return self._wrap(context, "web_search", payload)

        @function_tool
        async def explore_site(
            ctx: RunContextWrapper[RunContext],
            url: str,
            query: str,
            max_pages: int = DEFAULT_MAX_PAGES,
        ) -> str:
            """在單一網站內部尋找資訊：沿著網站選單、sitemap 與站內搜尋找出相關頁面並讀取內容。
            適合答案藏在官方網站或系所網站裡（課程、師資、規定、公告、招生資訊等），
            而一般搜尋找不到細節的情況。

            Args:
                url: 網站首頁或該單位的頁面網址，通常取自 discover 或 web_search 的結果。
                query: 想在網站裡找的內容，使用網站上可能出現的詞，例如「課程地圖 必修科目」。
                max_pages: 最多讀取幾個頁面（1～10）。
            """
            context = ctx.context
            if not context.consume_budget():
                return BUDGET_EXHAUSTED

            host = urlsplit(url if "//" in url else f"//{url}").netloc or url
            await context.emit(AgentEvent("tool", f"▹ 在 {truncate(host, 40)} 站內尋找「{truncate(query, 30)}」"))
            report = await self.explorer.explore(url, query, max_pages=max_pages)

            for page in report.get("pages_read", []):
                context.seen_urls.add(page["url"])
            for link in [*report.get("other_relevant_links", []), *report.get("site_menu", [])]:
                context.seen_urls.add(link["url"])
            return context.guard.wrap(f"explore_site:{host}", json.dumps(report, ensure_ascii=False))

        @function_tool
        async def web_fetch(ctx: RunContextWrapper[RunContext], url: str, find: str = "", start: int = 0) -> str:
            """讀取單一網頁或線上文件（HTML、PDF、DOCX、純文字）的內容。

            Args:
                url: 完整的 http 或 https 網址，通常取自搜尋或站內查找的結果。
                find: 想在頁面中找的關鍵字；有填時只回傳相關段落（適合很長的頁面），留空則從頭閱讀。
                start: 未填 find 時，從第幾個字開始讀取；長文件可依回傳的提示分段讀取。
            """
            context = ctx.context
            if not context.consume_budget():
                return BUDGET_EXHAUSTED

            await context.emit(AgentEvent("tool", f"▹ 閱讀 {truncate(urlsplit(url).netloc or url, 60)}"))
            result = await self.read_page(url, start=max(0, start), find=find)
            if not result.startswith("讀取網頁失敗"):
                context.seen_urls.add(url)
                for line in result.splitlines():
                    if line.startswith("URL: "):
                        context.seen_urls.add(line[5:].strip())
                    elif line.startswith("- ") and "：http" in line:
                        context.seen_urls.add(line[line.index("：http") + 1:].strip())
            return context.guard.wrap(f"web_fetch:{url}", result)

        if self.enabled:
            return [discover, web_search, explore_site, web_fetch]
        return [explore_site, web_fetch]

    @staticmethod
    def _wrap(context: RunContext, source: str, payload: dict[str, Any]) -> str:
        for entry in payload.get("results", []):
            context.seen_urls.add(entry["url"])
        for site in payload.get("sites", []):
            context.seen_urls.add(site["example"])
        return context.guard.wrap(source, json.dumps(payload, ensure_ascii=False))

    async def close(self) -> None:
        await self.engine.close()
        await self.fetcher.close()
