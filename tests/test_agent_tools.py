import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from agents.tool_context import ToolContext

from echo.ai.context import ChatOrigin, RunContext
from echo.ai.safety import PromptGuard
from echo.ai.tools import (
    ApiClient,
    build_channel_tools,
    build_fun_tools,
    build_knowledge_tools,
    build_note_tools,
    build_reminder_tools,
    build_time_tools,
    build_weather_tool,
)
from echo.ai.tools.fun import roll
from echo.storage.database import Database
from echo.storage.notes import NoteStore
from echo.storage.reminders import ReminderStore


def make_context(**kwargs) -> RunContext:
    return RunContext(user_id=1, guard=PromptGuard(), tool_budget=10, **kwargs)


async def invoke(tool, context, **args):
    ctx = ToolContext(context=context, tool_name=tool.name, tool_call_id="call", tool_arguments=json.dumps(args))
    return await tool.on_invoke_tool(ctx, json.dumps(args))


def by_name(tools):
    return {tool.name: tool for tool in tools}


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "t.db")
    await database.connect()
    yield database
    await database.close()


def test_roll_dice():
    for _ in range(50):
        result = roll("3d6+2")
        detail, total = result.split("：")[1].split(" = ")
        *rolls, modifier = (int(x) for x in detail.split(" + "))
        assert len(rolls) == 3 and all(1 <= r <= 6 for r in rolls) and modifier == 2
        assert int(total) == sum(rolls) + 2
    with pytest.raises(ValueError):
        roll("100d6")


async def test_random_pick_and_time_conversion():
    fun = by_name(build_fun_tools())
    result = await invoke(fun["random_pick"], make_context(), options=["拉麵", "壽司", "咖哩"], count=2)
    picked = result.removeprefix("抽選結果：").split("、")
    assert len(set(picked)) == 2 and set(picked) <= {"拉麵", "壽司", "咖哩"}

    time_tools = by_name(build_time_tools("Asia/Taipei"))
    converted = await invoke(time_tools["convert_time"], make_context(), time="2026-10-21 20:00", from_timezone="紐約", to_timezone="")
    assert "2026-10-22 08:00:00" in converted
    assert "未知的時區" in await invoke(time_tools["get_current_time"], make_context(), timezone="Mars/Base")


async def test_reminder_tools(db):
    store = ReminderStore(db)
    tools = by_name(build_reminder_tools(store, "Asia/Taipei"))
    context = make_context(origin=ChatOrigin(channel_id=42))

    result = await invoke(tools["set_reminder"], context, message="該喝水囉！", minutes_from_now=30)
    assert result.startswith("已設定提醒 #1")
    assert "必須在未來" in await invoke(tools["set_reminder"], context, message="x", time="2000-01-01 00:00")
    assert "#1" in await invoke(tools["list_reminders"], context)

    due = await store.pop_due(datetime.now(UTC) + timedelta(minutes=31))
    assert [(r.channel_id, r.content) for r in due] == [(42, "該喝水囉！")]
    assert await store.pop_due(datetime.now(UTC) + timedelta(days=1)) == []

    no_channel = make_context()
    assert "無法" in await invoke(tools["set_reminder"], no_channel, message="x", minutes_from_now=5)


async def test_note_tools(db):
    store = NoteStore(db)
    tools = by_name(build_note_tools(store))
    context = make_context()

    await invoke(tools["remember_note"], context, key="生日", value="3 月 14 日")
    await invoke(tools["remember_note"], context, key="生日", value="3 月 15 日")
    assert await store.get_all(1) == {"生日": "3 月 15 日"}
    assert "已忘記" in await invoke(tools["forget_note"], context, key="生日")
    assert await store.get_all(1) == {}


async def test_channel_tools():
    tools = by_name(build_channel_tools())
    # 對話彼此隔離：不提供讀取頻道歷史的工具
    assert set(tools) == {"add_reaction"}

    context = make_context(origin=ChatOrigin(channel_id=1))

    assert "已加上" in await invoke(tools["add_reaction"], context, emoji="❤️")
    assert "只能使用" in await invoke(tools["add_reaction"], context, emoji="@everyone")
    await invoke(tools["add_reaction"], context, emoji="😆")
    await invoke(tools["add_reaction"], context, emoji="🥺")
    assert "最多" in await invoke(tools["add_reaction"], context, emoji="🎉")
    assert context.reactions == ["❤️", "😆", "🥺"]


def api_with(handler) -> ApiClient:
    api = ApiClient(timeout=5)
    api._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return api


async def test_weather_tool():
    def handler(request):
        if "geocoding" in request.url.host:
            return httpx.Response(200, json={"results": [{"name": "臺北市", "country": "臺灣", "latitude": 25.05, "longitude": 121.5}]})
        return httpx.Response(200, json={
            "timezone": "Asia/Taipei",
            "current": {"time": "2026-10-21T15:00", "temperature_2m": 27.1, "apparent_temperature": 29.0,
                        "relative_humidity_2m": 70, "precipitation": 0, "weather_code": 2, "wind_speed_10m": 8},
            "daily": {"time": ["2026-10-21"], "weather_code": [61], "temperature_2m_max": [29],
                      "temperature_2m_min": [23], "precipitation_probability_max": [60], "uv_index_max": [6]},
        })

    api = api_with(handler)
    result = json.loads(await invoke(build_weather_tool(api), make_context(), location="台北", days=1))
    assert result["location"] == "臺北市, 臺灣"
    assert result["current"]["weather"] == "局部多雲" and result["forecast"][0]["weather"] == "小雨"
    await api.close()


async def test_wikipedia_and_currency_tools():
    def handler(request):
        if request.url.path.endswith("/search/page"):
            assert request.headers["accept-language"] == "zh-tw"
            return httpx.Response(200, json={"pages": [{"key": "鳴潮", "title": "鳴潮"}]})
        if "/page/summary/" in request.url.path:
            return httpx.Response(200, json={"title": "鳴潮", "extract": "開放世界動作遊戲",
                                             "content_urls": {"desktop": {"page": "https://zh.wikipedia.org/wiki/鳴潮"}}})
        if request.url.host == "open.er-api.com":
            return httpx.Response(200, json={"result": "success", "rates": {"TWD": 32.0}, "time_last_update_utc": "x"})
        return httpx.Response(404)

    api = api_with(handler)
    tools = by_name(build_knowledge_tools(api))
    context = make_context()

    wiki = await invoke(tools["wikipedia"], context, query="鳴潮", lang="zh")
    assert "開放世界動作遊戲" in wiki and "https://zh.wikipedia.org/wiki/鳴潮" in context.seen_urls

    money = json.loads(await invoke(tools["convert_currency"], context, amount=10, from_currency="usd", to_currency="twd"))
    assert money["result"] == 320.0
    bad = json.loads(await invoke(tools["convert_currency"], context, amount=1, from_currency="usd", to_currency="xyz"))
    assert "error" in bad
    await api.close()
