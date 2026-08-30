import json
from typing import Any

import httpx
from bs4 import BeautifulSoup
from agents import function_tool

_search_client: httpx.AsyncClient | None = None
_fetch_client: httpx.AsyncClient | None = None

_settings: Any = None


def set_web_search_settings(settings: Any) -> None:
    global _settings
    _settings = settings


async def _get_search_client(timeout: float) -> httpx.AsyncClient:
    global _search_client
    
    if _search_client is None:
        _search_client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        
    return _search_client


async def _get_fetch_client(timeout: float) -> httpx.AsyncClient:
    global _fetch_client
    
    if _fetch_client is None:
        _fetch_client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; EchoBot/1.0)"},
        )
        
    return _fetch_client


async def cleanup_web_search() -> None:
    global _search_client, _fetch_client
    if _search_client:
        await _search_client.aclose()
        _search_client = None
        
    if _fetch_client:
        await _fetch_client.aclose()
        _fetch_client = None

@function_tool
async def web_search(query: str) -> str:
    """搜尋網際網路上的最新資訊。"""
    try:
        client = await _get_search_client(_settings.search_timeout)
        
        response = await client.get(
            f"{_settings.searxng_url.rstrip('/')}/search",
            params={
                "q": query,
                "format": "json",
                "language": _settings.search_language,
            },
        )
        
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        
        results = [
            {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
            for r in data.get("results", [])[:_settings.search_max_results]
        ]
        return json.dumps(results, ensure_ascii=False, indent=2)
    
    except Exception as exc:
        return json.dumps({"error": f"搜尋失敗：{exc}"}, ensure_ascii=False)


@function_tool
async def web_fetch(url: str) -> str:
    """讀取指定 URL 的網頁內容。"""
    try:
        client = await _get_fetch_client(_settings.search_timeout)
        response = await client.get(url)
        response.raise_for_status()

        if "text/html" not in response.headers.get("content-type", "").lower():
            return f"URL: {url}\nContent-Type: {response.headers.get('content-type')}\n此資源不是 HTML 網頁。"

        soup = BeautifulSoup(response.text, "html.parser")
        for element in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
            element.decompose()

        text = "\n".join(line.strip() for line in soup.get_text(separator="\n", strip=True).splitlines() if line.strip())
        if len(text) > 20_000:
            text = text[:20_000] + "\n...[內容已截斷]"

        return f"URL: {url}\n\n{text}"
    
    except Exception as exc:
        return f"讀取網頁失敗：{exc}"
