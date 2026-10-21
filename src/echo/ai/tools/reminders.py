from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from agents import FunctionTool, RunContextWrapper, function_tool

from echo.ai.context import AgentEvent, RunContext
from echo.ai.tools.time import format_time
from echo.storage.reminders import ReminderStore

MAX_PENDING = 10
MAX_AHEAD = timedelta(days=366)
MIN_AHEAD = timedelta(minutes=1)
MAX_CONTENT = 500


def parse_due(time: str, minutes_from_now: int, tz: ZoneInfo, now: datetime) -> datetime:
    if minutes_from_now > 0:
        return now + timedelta(minutes=minutes_from_now)
    if not time.strip():
        raise ValueError("請提供 time 或 minutes_from_now")
    return datetime.strptime(time.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=tz)


def build_reminder_tools(store: ReminderStore, timezone: str) -> list[FunctionTool]:
    tz = ZoneInfo(timezone)

    @function_tool
    async def set_reminder(
        ctx: RunContextWrapper[RunContext],
        message: str,
        time: str = "",
        minutes_from_now: int = 0,
    ) -> str:
        """幫對方設定提醒，時間到時會在目前的頻道傳送提醒訊息。只在對方明確要求提醒時使用。

        Args:
            message: 時間到時要傳送的提醒內容，請以角色口吻撰寫，並包含要提醒的事項。
            time: 提醒時間，格式 YYYY-MM-DD HH:MM（預設時區）。與 minutes_from_now 擇一。
            minutes_from_now: 幾分鐘後提醒；使用 time 時填 0。
        """
        context = ctx.context
        if context.origin.channel_id is None:
            return "目前無法在這裡設定提醒。"

        now = datetime.now(UTC)
        try:
            due = parse_due(time, minutes_from_now, tz, now)
        except ValueError as error:
            return f"設定失敗：{error}"
        if due - now < MIN_AHEAD:
            return "設定失敗：提醒時間必須在未來（至少 1 分鐘後）。"
        if due - now > MAX_AHEAD:
            return "設定失敗：提醒時間不可超過一年。"
        if await store.count_user(context.user_id) >= MAX_PENDING:
            return f"設定失敗：每人最多 {MAX_PENDING} 個待處理提醒，請先取消部分提醒。"

        await context.emit(AgentEvent("tool", "⏰ 設定提醒"))
        reminder_id = await store.add(
            context.user_id, context.origin.channel_id, due, message.strip()[:MAX_CONTENT]
        )
        return f"已設定提醒 #{reminder_id}，將於 {format_time(due.astimezone(tz), tz)} 提醒。"

    @function_tool
    async def list_reminders(ctx: RunContextWrapper[RunContext]) -> str:
        """列出對方目前所有尚未觸發的提醒。"""
        reminders = await store.list_user(ctx.context.user_id)
        if not reminders:
            return "目前沒有任何提醒。"
        return "\n".join(
            f"#{r.id}｜{format_time(r.due_at.astimezone(tz), tz)}｜{r.content}" for r in reminders
        )

    @function_tool
    async def cancel_reminder(ctx: RunContextWrapper[RunContext], reminder_id: int) -> str:
        """取消對方的某個提醒。

        Args:
            reminder_id: 提醒編號，可先用 list_reminders 查詢。
        """
        if await store.cancel(ctx.context.user_id, reminder_id):
            return f"已取消提醒 #{reminder_id}。"
        return f"找不到提醒 #{reminder_id}。"

    return [set_reminder, list_reminders, cancel_reminder]
