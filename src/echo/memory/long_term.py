import logging
from typing import Any

import httpx

from echo.ai.models import ChatMessage
from echo.core.errors import MemoryServiceError

logger = logging.getLogger(__name__)


class LongTermMemory:
    """Mem0-compatible 長期記憶客戶端；未設定 base_url 時所有操作皆為 no-op。"""

    def __init__(self, base_url: str | None, timeout: float = 60.0):
        self.enabled = base_url is not None
        self.client = (
            httpx.AsyncClient(
                base_url=base_url,
                headers={"Content-Type": "application/json"},
                timeout=httpx.Timeout(timeout, connect=10.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
            if base_url is not None
            else None
        )

    async def add(self, messages: list[ChatMessage], user_id: int) -> None:
        await self._request(
            "POST",
            "/memories",
            json={
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "user_id": str(user_id),
            },
        )

    async def search(self, query: str, user_id: int, top_k: int = 5) -> list[str]:
        data = await self._request(
            "POST",
            "/search",
            json={
                "query": query,
                "filters": {"user_id": str(user_id)},
                "top_k": top_k,
            },
        )
        results = data.get("results", []) if isinstance(data, dict) else data or []
        return [item["memory"] for item in results if isinstance(item, dict) and item.get("memory")]

    async def delete_user(self, user_id: int) -> None:
        await self._request("DELETE", "/memories", params={"user_id": str(user_id)})

    async def delete_all(self) -> None:
        await self._request("DELETE", "/memories", params={"user_id": "*"})

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        if self.client is None:
            return {}

        try:
            response = await self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else {}
        except (httpx.HTTPError, ValueError) as error:
            logger.error("Memory service error: %s %s -> %s", method, path, error.__class__.__name__)
            raise MemoryServiceError("Long-term memory request failed") from error

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
