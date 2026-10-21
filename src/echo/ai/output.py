"""模型未依照 JSON schema 輸出時的寬鬆解析。

部分 OpenAI 相容代理（例如 Gemini）偶爾會以 ```json 程式碼框包裹、
在 JSON 前後加上文字、直接回覆純文字，或因輸出長度上限而把 JSON 截斷。
與其讓整個請求失敗，這裡盡量取回可用的內容。
"""

import json
import re
from typing import Any

from pydantic import ValidationError

from echo.ai.models import AIResponse, CharacterState
from echo.ai.streaming import PartialFieldExtractor

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


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _from_object(data: dict, fallback_state: CharacterState) -> AIResponse | None:
    dialogue = data.get("dialogue")
    if not isinstance(dialogue, str) or not dialogue.strip():
        return None
    try:
        state = CharacterState.model_validate(data.get("state"))
    except ValidationError:
        state = fallback_state
    return AIResponse(
        dialogue=dialogue,
        thoughts=_string_list(data.get("thoughts")),
        suggestions=_string_list(data.get("suggestions")),
        state=state,
        sources=_string_list(data.get("sources")),
    )


def salvage_response(text: str, fallback_state: CharacterState) -> AIResponse | None:
    """盡量把不合格式的輸出轉為 AIResponse；完全沒有內容時回傳 None。

    依序嘗試：完整 JSON → 被截斷的 JSON 取出 dialogue 欄位 → 純文字直接當對話。
    救回時角色狀態維持不變（除非 JSON 內有合法的 state）。
    """
    text = text.strip()
    if not text:
        return None

    data = _extract_json_object(text)
    if data is not None:
        # 是合法 JSON 但缺 dialogue，代表格式完全不符，不應把 JSON 原文當回覆
        return _from_object(data, fallback_state)

    stripped = CODE_FENCE.sub("", text)

    # JSON 被截斷而無法解析時，仍嘗試取出已生成的 dialogue 片段
    if stripped.lstrip().startswith("{"):
        partial = PartialFieldExtractor("dialogue").feed(stripped)
        if partial and partial.strip():
            return AIResponse(
                dialogue=partial, thoughts=[], suggestions=[], state=fallback_state, sources=[]
            )
        return None

    # 模型直接回覆純文字：當作 dialogue 使用
    return AIResponse(dialogue=stripped, thoughts=[], suggestions=[], state=fallback_state, sources=[])
