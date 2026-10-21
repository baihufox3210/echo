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
    assert ("edit", "你好 ▌", None) in log
    # 對話以編輯完成（不帶 embed），資訊卡另成獨立的第二則訊息
    assert ("edit", "你好呀", None) in log
    assert log[-1] == ("send", None, embed, False)


async def test_streamer_without_events_sends_split_chunks():
    source = FakeMessage()
    streamer = ReplyStreamer(source, interval=0.05)
    embed = discord.Embed(title="x")

    await streamer.finish("a" * 4100, embed=embed)

    log = source.channel.log
    # 三段對話各自成訊息，資訊卡再成獨立的第四則訊息
    assert [len(entry[1]) if entry[1] else 0 for entry in log] == [2000, 2000, 100, 0]
    assert [entry[3] for entry in log] == [True, False, False, False]
    assert [entry[2] for entry in log] == [None, None, None, embed]


def _result(*, affection=12, prev_affection=10, thoughts=None, suggestions=None, sources=None):
    return ChatResult(
        response=AIResponse(
            dialogue="hi", thoughts=thoughts or [], suggestions=suggestions or [], sources=[],
            state=CharacterState(affection=affection, mood=50, stamina=40, trust=5),
        ),
        previous_state=CharacterState(affection=prev_affection, mood=50, stamina=45, trust=5),
        sources=sources or [],
        usage=TokenUsage(requests=2, input_tokens=1000, output_tokens=234),
        tools_used=["web_search", "web_search", "web_fetch"],
    )


def test_meta_embed_shows_affection_delta_thoughts_and_suggestions():
    result = _result(
        thoughts=["她很開心", "想多聊一會"],
        suggestions=["留下來聽她唱歌", "問她最近在寫什麼"],
        sources=["https://example.com/a"],
    )
    embed = reply_embed(result, show_state=False)
    values = {field.name: field.value for field in embed.fields}

    # 預設只呈現好感度變動量，不含完整進度條
    assert values["♡ 好感度變動"] == "♡ **+2**（目前 12 / 100）"
    assert "♡ 好感度" not in values
    # 只用文字符號 ♡，不含會被轉成彩色圖示的 emoji
    assert "💗" not in "".join(values)
    assert "・她很開心" in values["※ 思緒摘要"]
    assert values["▸ 行動推薦"].startswith("A. 留下來聽她唱歌\nB. 問她最近在寫什麼")
    assert "[example.com](https://example.com/a)" in values["✎ 參考來源"]
    assert embed.footer.text == "工具：搜尋×2、閱讀網頁 · 1,234 tokens"


def test_meta_embed_full_state_when_enabled():
    embed = reply_embed(_result(), show_state=True)
    values = {field.name: field.value for field in embed.fields}
    assert "▲2" in values["♡ 好感度"] and "▼5" in values["✦ 體力"]


def test_meta_embed_absent_when_nothing_to_show():
    result = _result(affection=10, prev_affection=10)
    result.tools_used = []
    result.usage = TokenUsage()
    assert reply_embed(result, show_state=False) is None


def test_progress_bar():
    assert progress_bar(0) == "▱" * 10
    assert progress_bar(55) == "▰" * 6 + "▱" * 4


# Unicode 中帶有 emoji 屬性的 BMP 字元範圍（Discord 會將其轉為彩色圖示）
BMP_EMOJI = [
    (0x203C, 0x203C), (0x2049, 0x2049), (0x2194, 0x2199), (0x21A9, 0x21AA), (0x231A, 0x231B),
    (0x23E9, 0x23FA), (0x25AA, 0x25AB), (0x25B6, 0x25B6), (0x25C0, 0x25C0), (0x25FB, 0x25FE),
    (0x2600, 0x2604), (0x2614, 0x2615), (0x2620, 0x2623), (0x2640, 0x2642), (0x2648, 0x2653),
    (0x2660, 0x2660), (0x2663, 0x2663), (0x2665, 0x2666), (0x26A0, 0x26A1), (0x2705, 0x2705),
    (0x2708, 0x270D), (0x270F, 0x270F), (0x2714, 0x2714), (0x2728, 0x2728), (0x2764, 0x2764),
    (0x27A1, 0x27A1), (0x2B50, 0x2B55),
]


def has_emoji(text: str) -> bool:
    for char in text:
        code = ord(char)
        if code >= 0x1F000 or code == 0xFE0F or any(lo <= code <= hi for lo, hi in BMP_EMOJI):
            return True
    return False


def test_embeds_use_text_symbols_not_emoji():
    result = _result(thoughts=["a"], suggestions=["b"], sources=["https://example.com"])
    for embed in (reply_embed(result, show_state=False), reply_embed(result, show_state=True)):
        texts = [field.name for field in embed.fields] + [embed.footer.text or ""]
        texts += [field.value for field in embed.fields if not field.name.endswith("參考來源")]
        assert not any(has_emoji(t) for t in texts), texts
