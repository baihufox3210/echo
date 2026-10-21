import asyncio

import discord

from echo.ai.context import AgentEvent
from echo.ai.models import AIResponse, CharacterState, ChatResult, TokenUsage
from echo.core.embeds import progress_bar, reply_embed
from echo.core.reply import ReplyStreamer


class FakeSent:
    def __init__(self, log, content, embed):
        self.log = log
        self.content = content
        self.embed = embed

    async def edit(self, content=None, embed=None):
        self.log.append(("edit", content, embed))
        self.content, self.embed = content, embed


class FakeChannel:
    id = 1

    def __init__(self):
        self.log = []

    async def send(self, content, embed=None, reference=None, mention_author=None, allowed_mentions=None, delete_after=None):
        assert mention_author is False
        assert allowed_mentions.replied_user is False and allowed_mentions.everyone is False
        self.log.append(("send", content, embed, reference is not None))
        await asyncio.sleep(0.01)
        return FakeSent(self.log, content, embed)


class FakeMessage:
    def __init__(self):
        self.channel = FakeChannel()

    def to_reference(self, fail_if_not_exists=True):
        return object()


async def test_streamer_sends_once_then_edits_and_finishes_with_embed():
    source = FakeMessage()
    streamer = ReplyStreamer(source, interval=0.05)

    await streamer.handle(AgentEvent("tool", "🔍 搜尋"))
    await asyncio.sleep(0.02)
    await streamer.handle(AgentEvent("text", "你"))
    await streamer.handle(AgentEvent("text", "你好"))
    await asyncio.sleep(0.12)

    embed = discord.Embed(title="x")
    await streamer.finish("你好呀", embed=embed)

    log = source.channel.log
    assert log[0] == ("send", "🔍 搜尋", None, True)
    assert sum(1 for entry in log if entry[0] == "send") == 1
    assert ("edit", "你好 ▌", None) in log
    assert log[-1] == ("edit", "你好呀", embed)


async def test_streamer_without_events_sends_split_chunks():
    source = FakeMessage()
    streamer = ReplyStreamer(source, interval=0.05)
    embed = discord.Embed(title="x")

    await streamer.finish("a" * 4100, embed=embed)

    log = source.channel.log
    assert [len(entry[1]) for entry in log] == [2000, 2000, 100]
    assert [entry[3] for entry in log] == [True, False, False]
    assert log[-1][2] is embed and log[0][2] is None


def test_reply_embed_shows_state_changes_and_sources():
    result = ChatResult(
        response=AIResponse(
            dialogue="hi", action="", sources=[],
            state=CharacterState(affection=12, mood=50, stamina=40, trust=5),
        ),
        previous_state=CharacterState(affection=10, mood=50, stamina=45, trust=5),
        sources=["https://example.com/a"],
        usage=TokenUsage(requests=2, input_tokens=1000, output_tokens=234),
        tools_used=["web_search", "web_search", "web_fetch"],
    )
    embed = reply_embed(result, show_state=True)
    values = {field.name: field.value for field in embed.fields}

    assert "▲2" in values["💗 好感度"] and "▼5" in values["⚡ 體力"]
    assert "[example.com](https://example.com/a)" in values["📚 參考來源"]
    assert embed.footer.text == "🔧 搜尋×2、閱讀網頁 · 🪙 1,234 tokens"
    result.sources = []
    assert reply_embed(result, show_state=False) is None


def test_progress_bar():
    assert progress_bar(0) == "▱" * 10
    assert progress_bar(55) == "▰" * 6 + "▱" * 4
