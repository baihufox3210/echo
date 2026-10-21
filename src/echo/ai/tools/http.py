import logging
from typing import Any

import httpx

from echo.ai.tools.web_search import USER_AGENT, TTLCache

logger = logging.getLogger(__name__)


class ApiError(Exception):
    pass


class ApiClient:
    """呼叫公開 JSON API（天氣、維基百科、匯率）的共用客戶端，附帶短期快取。"""

    def __init__(self, timeout: float, cache_ttl: float = 600.0):
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        self._cache = TTLCache(cache_ttl)

    async def get_json(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        key = (url, tuple(sorted((params or {}).items())), tuple(sorted((headers or {}).items())))
        if (cached := self._cache.get(key)) is not None:
            return cached

        try:
            response = await self._client.get(url, params=params, headers=headers)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("API request failed: %s -> %s", url, error.__class__.__name__)
            raise ApiError(error.__class__.__name__) from error

        self._cache.set(key, data)
        return data

    async def close(self) -> None:
        await self._client.aclose()
