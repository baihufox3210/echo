import json

import httpx
import pytest

from echo.ai.tools.web_search import WebSearch, extract_html, is_public_address, normalize_url


def test_normalize_url_strips_tracking():
    assert normalize_url("https://Example.com/a/?utm_source=x&id=1#top") == "https://example.com/a?id=1"


@pytest.mark.parametrize(
    ("address", "expected"),
    [("8.8.8.8", True), ("127.0.0.1", False), ("10.0.0.5", False), ("169.254.169.254", False), ("::1", False)],
)
def test_is_public_address(address, expected):
    assert is_public_address(address) is expected


def test_extract_html_prefers_article():
    html = b"""<html><head><title>T</title><meta name="description" content="desc"></head>
    <body><nav>menu</nav><article><p>Hello</p><p>World</p></article><footer>f</footer></body></html>"""
    title, text = extract_html(html, None, 1000)
    assert title == "T"
    assert "Hello" in text and "World" in text and "menu" not in text and "desc" in text


async def test_search_formats_dedups_and_caches(settings):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.params["time_range"] == "week"
        return httpx.Response(200, json={"results": [
            {"title": "A", "url": "https://a.com/x?utm_source=1", "content": "first", "publishedDate": "2026-10-01T00:00:00"},
            {"title": "A dup", "url": "https://a.com/x", "content": "dup"},
            {"title": "B", "url": "https://b.com", "content": "second"},
        ]})

    web = WebSearch(settings)
    web._search_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    first = json.loads(await web.search("news", time_range="week"))
    second = json.loads(await web.search("news", time_range="week"))

    assert [r["title"] for r in first["results"]] == ["A", "B"]
    assert first["results"][0]["published"] == "2026-10-01"
    assert first == second and calls == 1
    await web.close()


async def test_fetch_blocks_private_addresses(settings):
    web = WebSearch(settings)
    assert "內部網路" in await web.fetch("http://127.0.0.1:8080/")
    assert "http/https" in await web.fetch("file:///etc/passwd")
    await web.close()


async def test_fetch_follows_redirects_and_extracts_text(settings):
    settings.fetch_allow_private = True

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        if request.url.path == "/new":
            html = "<html><head><title>新頁面</title></head><body><main>正文內容</main></body></html>"
            return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=html.encode())
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG")

    web = WebSearch(settings)
    web._fetch_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    page = await web.fetch("https://site.test/old")
    assert page.startswith("URL: https://site.test/new\n標題: 新頁面") and "正文內容" in page
    assert "不是可讀取的文字網頁" in await web.fetch("https://site.test/logo.png")
    await web.close()


async def test_search_reports_unresponsive_engines(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [], "unresponsive_engines": [["google", "timeout"]]})

    web = WebSearch(settings)
    web._search_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = json.loads(await web.search("anything"))
    assert result["results"] == [] and "note" in result
    await web.close()
