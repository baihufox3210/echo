from agents import FunctionTool, RunContextWrapper, function_tool

from echo.ai.context import BUDGET_EXHAUSTED, AgentEvent, RunContext
from echo.core.errors import EchoError
from echo.memory.long_term import LongTermMemory


def build_memory_tool(history: LongTermMemory, top_k: int) -> FunctionTool:
    @function_tool
    async def recall_memory(ctx: RunContextWrapper[RunContext], query: str) -> str:
        """回想與目前對話對象有關的過往記憶。當對方提到以前的事、約定或你想確認對方的喜好時使用。

        Args:
            query: 想回想的內容，例如「對方喜歡的食物」或「上次約好的事」。
        """
        context = ctx.context
        if not context.consume_budget():
            return BUDGET_EXHAUSTED

        await context.emit(AgentEvent("tool", "▹ 回想過去的記憶"))
        try:
            memories = await history.search(query=query, user_id=context.user_id, top_k=top_k)
        except EchoError:
            return "目前無法讀取長期記憶。"

        if not memories:
            return "沒有找到相關的記憶。"
        return context.guard.wrap("memory", "\n".join(f"- {m}" for m in memories))

    return recall_memory
