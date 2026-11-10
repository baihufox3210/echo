"""SearXNG 搜尋：查詢組合、結果整理、多查詢合併與模糊探索。"""

import asyncio
import logging
from typing import Any, Literal

import httpx

from echo.ai.search.common import (
    USER_AGENT,
    TTLCache,
    clean_domain,
    host_matches,
    hostname,
    normalize_url,
    truncate,
)
from echo.ai.search.text import candidate_names, query_terms, relevance, title_coverage
from echo.core.config import Settings

logger = logging.getLogger(__name__)

SNIPPET_MAX_CHARS = 400
MAX_PARALLEL_QUERIES = 4
RRF_K = 60
# 探索階段額外嘗試的查詢後綴，用來找出官方網站與正式名稱
DISCOVER_SUFFIXES = ("", "官網", "是什麼")
OFFICIAL_TLDS = (".edu.tw", ".gov.tw", ".edu", ".gov", ".ac.jp", ".ac.uk", ".org.tw")
# 常見的非官方彙整／社群網站，不視為官方來源
AGGREGATOR_DOMAINS = (
    "facebook.com", "instagram.com", "youtube.com", "dcard.tw", "ptt.cc", "threads.net", "x.com",
    "twitter.com", "wikipedia.org", "104.com.tw", "1111.com.tw", "collego.edu.tw", "tiktok.com",
)

TimeRange = Literal["any", "day", "week", "month", "year"]
Category = Literal["general", "news", "science", "it", "videos"]


def build_query(
    query: str,
    site: str = "",
    exact_phrase: str = "",
    filetype: str = "",
    exclude_sites: list[str] | None = None,
    exclude_words: list[str] | None = None,
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


def is_official(url: str) -> bool:
    host = hostname(url)
    return host.endswith(OFFICIAL_TLDS) and not any(host_matches(url, d) for d in AGGREGATOR_DOMAINS)


class SearchEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = settings.searxng_url.rstrip("/") if settings.searxng_url else None
        self._cache = TTLCache(settings.search_cache_ttl)
        self._client = httpx.AsyncClient(
            timeout=settings.search_timeout,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    @property
    def enabled(self) -> bool:
        return self.base_url is not None

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
        if self.base_url is None:
            return {"error": "網路搜尋功能未啟用"}

        limit = min(max_results or self.settings.search_max_results, 20)
        page = min(max(page, 1), 5)
        language = language.strip() or self.settings.search_language
        cache_key = (query, time_range, category, language, page, limit, site, tuple(exclude_sites or ()))
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
            data = await self._request(params)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Web search failed: %s", exc.__class__.__name__)
            return {"error": f"搜尋失敗：{exc.__class__.__name__}"}

        payload = self._format(query, data, limit, site=site, exclude_sites=exclude_sites)
        if payload["results"] or payload.get("answers"):
            self._cache.set(cache_key, payload)

        logger.info("Web search: query=%r results=%d", query, len(payload["results"]))
        return payload

    async def search_many(
        self,
        queries: list[str],
        max_results: int | None = None,
        **options: Any,
    ) -> dict[str, Any]:
        """並行執行多個查詢，以 Reciprocal Rank Fusion 合併排序。"""
        queries = list(dict.fromkeys(q.strip() for q in queries if q.strip()))[:MAX_PARALLEL_QUERIES]
        if not queries:
            return {"error": "至少需要一個查詢"}
        if len(queries) == 1:
            return await self.search(queries[0], max_results=max_results, **options)

        payloads = await asyncio.gather(*(self.search(q, max_results=10, **options) for q in queries))

        scores: dict[str, float] = {}
        entries: dict[str, dict[str, Any]] = {}
        answers: list[str] = []
        for payload in payloads:
            answers.extend(payload.get("answers", []))
            for rank, entry in enumerate(payload.get("results", [])):
                key = normalize_url(entry["url"])
                scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
                entries.setdefault(key, {**entry, "matched_queries": []})["matched_queries"].append(payload["query"])

        limit = max_results or self.settings.search_max_results + 3
        ranked = sorted(entries, key=lambda k: scores[k], reverse=True)
        merged: dict[str, Any] = {"queries": queries, "results": [entries[k] for k in ranked[:limit]]}
        if answers:
            merged["answers"] = list(dict.fromkeys(answers))[:3]
        errors = [p["error"] for p in payloads if "error" in p]
        if errors and not merged["results"]:
            merged["error"] = errors[0]
        return merged

    async def discover(self, topic: str) -> dict[str, Any]:
        """第一階段的模糊探索：找出主題的正式名稱、官方網站與相關條目。

        使用者常用簡稱或不精確的說法（例如「輔大智慧資安」），直接搜尋細節容易失準。
        這裡同時以數種說法搜尋，從結果標題歸納出可能的正式名稱，
        並標出官方網域，供下一步精確搜尋或站內查找使用。
        """
        topic = topic.strip()
        if not topic:
            return {"error": "主題不可為空"}

        merged = await self.search_many(
            [f"{topic} {suffix}".strip() for suffix in DISCOVER_SUFFIXES], max_results=12
        )
        results = merged.get("results", [])
        if not results:
            return {"topic": topic, "results": [], **{k: v for k, v in merged.items() if k in ("error", "note")}}

        terms = query_terms(topic)
        for entry in results:
            text = f"{entry['title']} {entry['snippet']}"
            entry["official"] = is_official(entry["url"])
            # 簡稱比對：主題的字是否依序出現在標題中
            community = any(host_matches(entry["url"], d) for d in AGGREGATOR_DOMAINS)
            entry["_score"] = (
                relevance(terms, text)
                + 2.0 * title_coverage(topic, entry["title"])
                # 探索階段以辨識對象為主：官方來源優先，論壇與社群貼文靠後
                + (2.5 if entry["official"] else 0.0)
                - (1.5 if community else 0.0)
            )
        results.sort(key=lambda e: e["_score"], reverse=True)

        sites: dict[str, dict[str, Any]] = {}
        for entry in results:
            host = hostname(entry["url"])
            if any(host_matches(entry["url"], d) for d in AGGREGATOR_DOMAINS):
                continue
            site = sites.setdefault(
                host,
                {"site": host, "official": entry["official"], "pages": 0, "example": entry["url"], "title": entry["title"]},
            )
            site["pages"] += 1
            # 以最接近首頁的網址作為進入點
            if len(entry["url"]) < len(site["example"]):
                site["example"], site["title"] = entry["url"], entry["title"]
        ranked_sites = sorted(sites.values(), key=lambda s: (s["official"], s["pages"]), reverse=True)

        for entry in results:
            entry.pop("_score", None)
            entry.pop("matched_queries", None)

        payload: dict[str, Any] = {
            "topic": topic,
            "candidate_names": candidate_names(topic, (e["title"] for e in results)),
            "sites": ranked_sites[:6],
            "results": results[:8],
            "next_steps": (
                "從 candidate_names 選出最符合的正式名稱，用 web_search 以正式名稱加上想查的細節再搜尋；"
                "若答案可能在官方網站內，用 explore_site 指定 sites 中的網址與想找的內容。"
            ),
        }
        if merged.get("answers"):
            payload["answers"] = merged["answers"]
        return payload

    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        """查詢 SearXNG；遇到連線錯誤、429 或 5xx 時重試一次。"""
        attempts = 2
        for attempt in range(attempts):
            try:
                response = await self._client.get(f"{self.base_url}/search", params=params)
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
    def _format(
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

    async def close(self) -> None:
        await self._client.aclose()
