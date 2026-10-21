import unicodedata

from agents import FunctionTool, RunContextWrapper, function_tool

from echo.ai.context import MAX_REACTIONS, AgentEvent, RunContext

MAX_HISTORY = 50


def is_emoji(text: str) -> bool:
    """僅接受 Unicode emoji（含膚色、ZWJ 組合），避免被用來傳送任意文字。"""
    if not text or len(text) > 12:
        return False
    for char in text:
        if char in "‍️⃣":
            continue
        category = unicodedata.category(char)
        if category not in ("So", "Sk") and not 0x1F1E6 <= ord(char) <= 0x1F1FF:
            return False
    return True


def build_channel_tools() -> list[FunctionTool]:
    @function_tool
    async def read_channel_history(ctx: RunContextWrapper[RunContext], limit: int = 20) -> str:
        """讀取目前頻道最近的對話（包含其他人的訊息），用於理解群組聊天的前後文，
        例如對方說「你看上面」或詢問大家剛剛在聊什麼時。

        Args:
            limit: 要讀取的訊息數量，1~50。
        """
        context = ctx.context
        reader = context.origin.read_history
        if reader is None:
            return "目前無法讀取這個頻道的對話。"

        await context.emit(AgentEvent("tool", "👀 看看大家剛剛聊了什麼"))
        try:
            messages = await reader(min(max(limit, 1), MAX_HISTORY))
        except Exception:
            return "讀取頻道對話失敗（可能缺少讀取訊息歷史的權限）。"
        if not messages:
            return "頻道中沒有更早的訊息。"

        lines = [
            f"[{m.created_at}] {m.author}{'（你自己）' if m.is_bot else ''}：{m.content}"
            for m in messages
        ]
        return context.guard.wrap("channel_history", "\n".join(lines))

    @function_tool
    async def add_reaction(ctx: RunContextWrapper[RunContext], emoji: str) -> str:
        """對對方的訊息加上表情反應，用來表達角色當下的情緒（可與文字回覆同時使用）。

        Args:
            emoji: 單一 Unicode 表情符號，例如 ❤️、😆、🥺。
        """
        context = ctx.context
        emoji = emoji.strip()
        if not is_emoji(emoji):
            return "只能使用單一 Unicode 表情符號。"
        if len(context.reactions) >= MAX_REACTIONS:
            return f"每則訊息最多加上 {MAX_REACTIONS} 個表情反應。"
        if emoji not in context.reactions:
            context.reactions.append(emoji)
        return f"已加上表情反應 {emoji}"

    return [read_channel_history, add_reaction]
