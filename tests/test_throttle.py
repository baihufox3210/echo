import asyncio
import time

import httpx
from agents import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from echo.ai.client import AIClient
from echo.ai.context import RunContext
from echo.ai.models import CharacterState, ChatMessage
from echo.ai.safety import PromptGuard
from echo.ai.throttle import ApiThrottle, parse_retry_after
from echo.core.errors import AIOverloadedError
from echo.memory.long_term import LongTermMemory

REQUEST = httpx.Request("POST", "http://fake/v1/chat/completions")


def response(status, headers=None):
    return httpx.Response(status, headers=headers, request=REQUEST)


def test_parse_retry_after():
    assert parse_retry_after("7") == 7.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("garbage") is None
    assert 0 <= parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") <= 1


async def test_429_pauses_following_requests():
    throttle = ApiThrottle(cooldown=0.3)
    await throttle.after_response(response(429))

    start = time.monotonic()
    await throttle.before_request(REQUEST)
    assert time.monotonic() - start >= 0.25


async def test_retry_after_header_overrides_default_cooldown():
    throttle = ApiThrottle(cooldown=5)
    await throttle.after_response(response(429, {"retry-after": "0.2"}))

    start = time.monotonic()
    await throttle.before_request(REQUEST)
    assert 0.15 <= time.monotonic() - start < 1


async def test_successful_responses_do_not_pause():
    throttle = ApiThrottle(cooldown=5)
    await throttle.after_response(response(200))

    start = time.monotonic()
    await throttle.before_request(REQUEST)
    assert time.monotonic() - start < 0.05


async def test_requests_per_minute_limit(monkeypatch):
    throttle = ApiThrottle(requests_per_minute=2)
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        throttle._recent.clear()  # 模擬時間窗口已過

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    for _ in range(3):
        await throttle.before_request(REQUEST)

    assert len(slept) == 1 and 59 < slept[0] <= 60


async def test_rate_limited_message_no_longer_multiplies_requests(settings):
    """429 時只由 SDK 重試（預設共 3 次），外層不再重跑整個代理（以前會變成 6 次）。"""
    settings.ai_rate_limit_cooldown = 0.1
    calls = []

    def handler(request):
        calls.append(time.monotonic())
        return httpx.Response(429, json={"error": {"message": "quota"}})

    client = AIClient(settings, LongTermMemory(None), notes=None, reminders=None)
    hooks = {"request": [client.throttle.before_request], "response": [client.throttle.after_response]}
    client.client = AsyncOpenAI(
        api_key="k", base_url="http://fake/v1", max_retries=settings.ai_max_retries,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), event_hooks=hooks),
    )
    client.agent.model = OpenAIChatCompletionsModel(model=settings.model, openai_client=client.client)

    events = []

    async def on_event(event):
        events.append(event.text)

    context = RunContext(
        user_id=1, guard=PromptGuard(), tool_budget=5, on_event=on_event,
        fallback_state=CharacterState(affection=0, mood=50, stamina=100, trust=0),
    )

    try:
        await client.chat([ChatMessage("system", "s"), ChatMessage("user", "hi")], context)
    except AIOverloadedError:
        pass
    else:
        raise AssertionError("expected AIOverloadedError")

    assert len(calls) == settings.ai_max_retries + 1
    # 每次重試之間至少間隔冷卻時間
    assert all(b - a >= 0.09 for a, b in zip(calls, calls[1:]))
    await client.close()


def test_client_uses_throttled_http_client(settings):
    client = AIClient(settings, LongTermMemory(None), notes=None, reminders=None)
    hooks = client.client._client.event_hooks
    assert client.throttle.before_request in hooks["request"]
    assert client.throttle.after_response in hooks["response"]
