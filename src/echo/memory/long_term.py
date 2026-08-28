import logging

import httpx

from typing import Any

from echo.ai.models import ChatMessage


logger = logging.getLogger(__name__)


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
        response = await self.client.post(
            "/memories",
            json={
                "messages": [
                    {
                        "role": message.role,
                        "content": message.content,
                    }
                    for message in messages
                ],
                "user_id": str(user_id),
            },
        )
        
        response.raise_for_status()
        return response.json()
    
    async def search(self, query: str, user_id: int, top_k: int = 5) -> dict[str, Any]:
        response = await self.client.post(
            "/search",
            json={
                "query": query,
                "filters": {
                    "user_id": str(user_id),
                },
                "top_k": top_k,
            },
        )
        
        response.raise_for_status()
        return response.json()
    
    async def get_all(self, user_id: int) -> dict[str, Any]:
        response = await self.client.get(
            "/memories",
            params={
                "user_id": str(user_id),
            },
        )
        
        response.raise_for_status()
        return response.json()
    
    async def delete_user(self, user_id: int) -> dict[str, Any]:
        response = await self.client.delete(
            "/memories",
            params={
                "user_id": str(user_id),
            },
        )

        response.raise_for_status()
        return response.json()
    
    async def delete_all(self) -> dict[str, Any]:
        try:
            response = await self.client.delete(
                "/memories",
                params={
                    "user_id": "*",
                },
            )

            response.raise_for_status()
            return response.json()
        except httpx.HTTPError:
            logger.exception("Failed to delete all memories")
            return {"error": "Failed to delete all memories"}
        
    async def close(self):
        await self.client.aclose()