from collections import Counter
from urllib.parse import urlsplit

import discord

from echo.ai.models import CharacterState, ChatResult
from echo.storage.usage import UsageSummary

STATE_FIELDS = (
    ("affection", "💗 好感度"),
    ("trust", "🤝 信任度"),
    ("mood", "🎵 心情"),
    ("stamina", "⚡ 體力"),
)
TOOL_LABELS = {
    "web_search": "搜尋",
    "multi_search": "多重搜尋",
    "web_fetch": "閱讀網頁",
    "deep_research": "深入研究",
    "recall_memory": "回想記憶",
    "read_attachment": "讀取附件",
    "calculate": "計算",
    "get_current_time": "查看時間",
    "convert_time": "時區換算",
    "get_weather": "天氣",
    "wikipedia": "維基百科",
    "convert_currency": "匯率",
    "roll_dice": "擲骰",
    "random_pick": "抽選",
    "remember_note": "記筆記",
    "forget_note": "刪除筆記",
    "set_reminder": "設定提醒",
    "list_reminders": "查看提醒",
    "cancel_reminder": "取消提醒",
    "read_channel_history": "閱讀頻道",
    "add_reaction": "表情反應",
}
EMBED_COLOR = discord.Color.from_rgb(255, 143, 177)


def progress_bar(value: int, maximum: int = 100, length: int = 10) -> str:
    filled = round(max(0, min(value, maximum)) / maximum * length)
    return "▰" * filled + "▱" * (length - filled)


def delta_text(before: int, after: int) -> str:
    diff = after - before
    if diff > 0:
        return f" `▲{diff}`"
    if diff < 0:
        return f" `▼{-diff}`"
    return ""


def add_state_fields(embed: discord.Embed, state: CharacterState, previous: CharacterState | None = None) -> None:
    for key, label in STATE_FIELDS:
        value = getattr(state, key)
        change = delta_text(getattr(previous, key), value) if previous else ""
        embed.add_field(name=label, value=f"{progress_bar(value)} **{value}**{change}", inline=True)


def _affection_change(before: int, after: int) -> str:
    diff = after - before
    arrow = f"**+{diff}**" if diff > 0 else (f"**{diff}**" if diff < 0 else "無變動")
    return f"{arrow}（目前 {after} / 100）"


def reply_embed(result: ChatResult, show_state: bool) -> discord.Embed | None:
    """第二則訊息的資訊卡：好感度變動、思緒摘要、行動推薦與引用來源。

    show_state 為 True 時額外附上完整狀態進度條；預設只呈現好感度變動量，
    完整狀態數值改由 /memory status 查看。
    """
    response = result.response
    affection_changed = result.previous_state.affection != response.state.affection

    # 沒有任何值得單獨成一則訊息的內容時，不送出第二則
    has_content = affection_changed or response.thoughts or response.suggestions or result.sources
    if not show_state and not has_content:
        return None

    embed = discord.Embed(color=EMBED_COLOR)

    if show_state:
        add_state_fields(embed, response.state, result.previous_state)
    elif affection_changed:
        embed.add_field(
            name="💗 好感度變動",
            value=_affection_change(result.previous_state.affection, response.state.affection),
            inline=False,
        )

    if response.thoughts:
        embed.add_field(
            name="💭 思緒摘要",
            value="\n".join(f"・{thought}" for thought in response.thoughts)[:1024],
            inline=False,
        )

    if response.suggestions:
        options = (f"{chr(65 + i)}. {text}" for i, text in enumerate(response.suggestions))
        embed.add_field(name="🎬 行動推薦", value="\n".join(options)[:1024], inline=False)

    if result.sources:
        lines = [
            f"{index}. [{discord.utils.escape_markdown(urlsplit(url).netloc or url)}]({url})"
            for index, url in enumerate(result.sources, start=1)
        ]
        embed.add_field(name="📚 參考來源", value="\n".join(lines)[:1024], inline=False)

    footer = []
    if result.tools_used:
        counts = Counter(TOOL_LABELS.get(name, name) for name in result.tools_used)
        footer.append("🔧 " + "、".join(f"{name}×{n}" if n > 1 else name for name, n in counts.items()))
    if result.usage.total_tokens:
        footer.append(f"🪙 {result.usage.total_tokens:,} tokens")
    if footer:
        embed.set_footer(text=" · ".join(footer))

    return embed if embed.fields or embed.footer.text else None


def usage_lines(summary: UsageSummary) -> str:
    return (
        f"訊息 **{summary.messages:,}** · API 請求 **{summary.requests:,}**\n"
        f"輸入 {summary.input_tokens:,} · 輸出 {summary.output_tokens:,} · 合計 **{summary.total_tokens:,}**"
    )
