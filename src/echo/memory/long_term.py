import httpx

from typing import Any

from echo.ai.models import ChatMessage
from echo.core.errors import MemoryServiceError

class LongTermMemory:
    def __init__(self, base_url: str, timeout: float = 50):        
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Content-Type": "application/json",
            },
            timeout=timeout
        )
        
    async def add(self, messages: list[ChatMessage], user_id: int) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/memories",
            json={
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in messages
                ],
                "user_id": str(user_id),
            },
        )
    
    async def search(self, query: str, user_id: int, top_k: int = 5) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/search",
            json={
                "query": query,
                "filters": {
                    "user_id": str(user_id),
                },
                "top_k": top_k,
            },
        )
    
    async def get_all(self, user_id: int) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/memories",
            params={
                "user_id": str(user_id),
            },
        )   
    
    async def delete_user(self, user_id: int) -> dict[str, Any]:
        return await self._request(
            "DELETE",
            "/memories",
            params={
                "user_id": str(user_id),
            },
        )
    
    async def delete_all(self) -> dict[str, Any]:
        return await self._request(
            "DELETE",
            "/memories",
            params={"user_id": "*"},
        )

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        try:
            response = await self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise MemoryServiceError("Long-term memory request failed") from error
        
    async def close(self):
        await self.client.aclose()