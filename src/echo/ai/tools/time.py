from datetime import datetime
from zoneinfo import ZoneInfo

from agents import FunctionTool, function_tool

WEEKDAYS = "一二三四五六日"


def now_text(tz: ZoneInfo) -> str:
    now = datetime.now(tz)
    return f"{now:%Y-%m-%d %H:%M:%S}（星期{WEEKDAYS[now.weekday()]}，{tz.key}）"


def build_time_tool(timezone: str) -> FunctionTool:
    tz = ZoneInfo(timezone)

    @function_tool
    def get_current_time() -> str:
        """取得目前的日期、時間與星期。"""
        return now_text(tz)

    return get_current_time
