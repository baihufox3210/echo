"""模型未依照 JSON schema 輸出時的寬鬆解析。

部分 OpenAI 相容代理（例如 Gemini）偶爾會以 ```json 程式碼框包裹、
在 JSON 前後加上文字，或乾脆直接回覆純文字。與其讓整個請求失敗，
這裡盡量取回可用的內容。
"""

import json
import re
from typing import Any

from pydantic import ValidationError

from echo.ai.models import AIResponse, CharacterState

CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def response_text(raw_responses: list[Any]) -> str:
    """從 openai-agents 的 ModelResponse 取出最後一次回應的文字內容。"""
    for response in reversed(raw_responses):
        parts = []
        for item in getattr(response, "output", []):
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", []):
                if text := getattr(content, "text", None):
                    parts.append(text)
        if parts:
            return "".join(parts)
    return ""


def _extract_json_object(text: str) -> dict | None:
    stripped = CODE_FENCE.sub("", text.strip())
    candidates = [stripped]
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start:end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def salvage_response(text: str, fallback_state: CharacterState) -> AIResponse | None:
    """盡量把不合格式的輸出轉為 AIResponse；完全沒有內容時回傳 None。"""
    text = text.strip()
    if not text:
        return None

    data = _extract_json_object(text)
    if data is not None:
        dialogue = data.get("dialogue")
        if isinstance(dialogue, str) and dialogue.strip():
            try:
                state = CharacterState.model_validate(data.get("state"))
            except ValidationError:
                state = fallback_state
            sources = data.get("sources")
            return AIResponse(
                dialogue=dialogue,
                action=str(data.get("action") or ""),
                state=state,
                sources=[s for s in sources if isinstance(s, str)] if isinstance(sources, list) else [],
            )
        # 是 JSON 卻沒有 dialogue，代表格式完全不符，不能把 JSON 原文當成回覆
        return None

    # 模型直接回覆純文字：當作 dialogue 使用，角色狀態維持不變
    return AIResponse(dialogue=CODE_FENCE.sub("", text), action="", state=fallback_state, sources=[])
