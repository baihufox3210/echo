import json

import httpx
import pytest
from agents import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from echo.ai.client import AIClient
from echo.ai.context import RunContext
from echo.ai.models import CharacterState, ChatMessage
from echo.ai.output import salvage_response
from echo.ai.safety import PromptGuard
from echo.ai.streaming import DialogueStream
from echo.core.errors import AIOverloadedError
from echo.memory.long_term import LongTermMemory

STATE = CharacterState(affection=7, mood=50, stamina=80, trust=3)
VALID = {"dialogue": "你好～", "thoughts": [], "suggestions": [], "state": {"affection": 8, "mood": 55, "stamina": 80, "trust": 3}, "sources": []}


def test_salvage_fenced_json():
    result = salvage_response(f"```json\n{json.dumps(VALID, ensure_ascii=False)}\n```", STATE)
    assert result.dialogue == "你好～" and result.state.affection == 8


def test_salvage_json_with_surrounding_text_and_missing_fields():
    result = salvage_response('好的，以下是回覆：{"dialogue": "嗨"} 希望有幫助', STATE)
    assert result.dialogue == "嗨" and result.state == STATE and result.sources == []


def test_salvage_plain_text_keeps_state():
    result = salvage_response("這是一張貓咪的照片喔！", STATE)
    assert result.dialogue == "這是一張貓咪的照片喔！" and result.state == STATE


def test_salvage_rejects_json_without_dialogue_and_empty():
    assert salvage_response('{"answer": "x"}', STATE) is None
    assert salvage_response("   ", STATE) is None


def test_salvage_truncated_json_recovers_partial_dialogue():
    # 模型因輸出上限而把 JSON 截斷在 dialogue 中途（本次實際遇到的情況）
    truncated = '{\n  "dialogue": "(看著檔案)\\n\\n『愛彌斯幫你整理好了：\\n1. 被動情蒐\\n2. 主動情蒐 (`-A`'
    result = salvage_response(truncated, STATE)
    assert result is not None
    assert result.dialogue.startswith("(看著檔案)")
    assert "被動情蒐" in result.dialogue
    assert "dialogue" not in result.dialogue and "{" not in result.dialogue
    assert result.state == STATE


def test_dialogue_stream_handles_json_and_plain_text():
    stream = DialogueStream()
    assert stream.feed('{"dialogue": "你') == "你"
    stream.reset()
    assert stream.feed("這是") is None
    assert stream.feed("一張可愛的貓咪照片") == "這是一張可愛的貓咪照片"


def completion(content):
    return {
        "id": "x", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def sse(text):
    chunks = [text[i:i + 5] for i in range(0, len(text), 5)]
    body = "".join(
        "data: " + json.dumps({"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
                               "choices": [{"index": 0, "delta": {"role": "assistant", "content": c}, "finish_reason": None}]}) + "\n\n"
        for c in chunks
    )
    body += "data: " + json.dumps({"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
                                   "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + "\n\n"
    return body + "data: [DONE]\n\n"


def make_client(settings, handler):
    client = AIClient(settings, LongTermMemory(None), notes=None, reminders=None)
    client.client = AsyncOpenAI(
        api_key="k", base_url="http://fake/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    client.agent.model = OpenAIChatCompletionsModel(model=settings.model, openai_client=client.client)
    if client.fallback_agent is not None:
        client.fallback_agent.model = OpenAIChatCompletionsModel(model=settings.fallback_model, openai_client=client.client)
    return client


def context():
    return RunContext(user_id=1, guard=PromptGuard(), tool_budget=5, fallback_state=STATE)


MESSAGES = [ChatMessage("system", "sys"), ChatMessage("user", "這是什麼")]


@pytest.mark.parametrize("stream", [False, True])
async def test_plain_text_output_is_salvaged(settings, stream):
    def handler(request):
        if json.loads(request.content).get("stream"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse("這是一張貓咪的照片喔！"))
        return httpx.Response(200, json=completion("這是一張貓咪的照片喔！"))

    events = []
    ctx = context()

    async def on_event(event):
        events.append(event.text)

    ctx.on_event = on_event
    client = make_client(settings, handler)
    outcome = await client.chat(MESSAGES, ctx, stream=stream)

    assert outcome.response.dialogue == "這是一張貓咪的照片喔！"
    assert outcome.response.state == STATE
    if stream:
        assert len(events) > 1 and events[-1] == "這是一張貓咪的照片喔！"
    await client.close()


async def test_overload_switches_to_fallback_model(settings):
    settings.fallback_model = "backup-model"
    models = []

    def handler(request):
        body = json.loads(request.content)
        models.append(body["model"])
        if body["model"] == settings.model:
            return httpx.Response(503, json={"error": {"code": 503, "message": "high demand"}})
        return httpx.Response(200, json=completion(json.dumps(VALID, ensure_ascii=False)))

    settings.ai_max_runs = 1
    client = make_client(settings, handler)
    outcome = await client.chat(MESSAGES, context())

    assert models == [settings.model, "backup-model"]
    assert outcome.response.dialogue == "你好～"
    await client.close()


async def test_overload_without_fallback_raises_overloaded(settings):
    def handler(request):
        return httpx.Response(503, json={"error": {"code": 503, "message": "high demand"}})

    client = make_client(settings, handler)
    with pytest.raises(AIOverloadedError):
        await client.chat(MESSAGES, context())
    await client.close()


async def test_streaming_requests_and_records_token_usage(settings):
    """非 OpenAI 官方的相容端點，只有請求帶 stream_options.include_usage 才會回傳用量。"""
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        text = json.dumps(VALID, ensure_ascii=False)
        chunk = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m"}
        events = [
            {**chunk, "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]},
            {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        if (body.get("stream_options") or {}).get("include_usage"):
            events.append({**chunk, "choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}})
        payload = "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=payload)

    client = make_client(settings, handler)
    outcome = await client.chat(MESSAGES, context(), stream=True)

    assert requests[0]["stream_options"] == {"include_usage": True}
    assert (outcome.usage.requests, outcome.usage.input_tokens, outcome.usage.output_tokens) == (1, 120, 30)
    await client.close()


async def test_stream_include_usage_can_be_disabled(settings):
    settings.stream_include_usage = False
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(json.dumps(VALID, ensure_ascii=False)))

    client = make_client(settings, handler)
    await client.chat(MESSAGES, context(), stream=True)
    assert "stream_options" not in requests[0] or not requests[0]["stream_options"].get("include_usage")
    await client.close()
