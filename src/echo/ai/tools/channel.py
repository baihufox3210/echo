import unicodedata

from agents import FunctionTool, RunContextWrapper, function_tool

from echo.ai.context import MAX_REACTIONS, RunContext



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

    return [add_reaction]
