from datetime import datetime
from zoneinfo import ZoneInfo

from agents import function_tool

@function_tool
def get_current_time() -> str:
    """取得目前時間（台灣時區）"""
    taiwan_tz = ZoneInfo("Asia/Taipei")
    return datetime.now(taiwan_tz).strftime("%Y-%m-%d %H:%M:%S")