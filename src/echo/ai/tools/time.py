from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agents import FunctionTool, function_tool

WEEKDAYS = "一二三四五六日"

# 常見地名對應的時區，方便模型直接使用中文名稱
TIMEZONE_ALIASES = {
    "台灣": "Asia/Taipei", "台北": "Asia/Taipei", "日本": "Asia/Tokyo", "東京": "Asia/Tokyo",
    "韓國": "Asia/Seoul", "首爾": "Asia/Seoul", "香港": "Asia/Hong_Kong", "中國": "Asia/Shanghai",
    "上海": "Asia/Shanghai", "北京": "Asia/Shanghai", "新加坡": "Asia/Singapore", "泰國": "Asia/Bangkok",
    "曼谷": "Asia/Bangkok", "印度": "Asia/Kolkata", "英國": "Europe/London", "倫敦": "Europe/London",
    "法國": "Europe/Paris", "巴黎": "Europe/Paris", "德國": "Europe/Berlin", "柏林": "Europe/Berlin",
    "紐約": "America/New_York", "美東": "America/New_York", "洛杉磯": "America/Los_Angeles",
    "美西": "America/Los_Angeles", "雪梨": "Australia/Sydney", "澳洲": "Australia/Sydney",
    "UTC": "UTC", "GMT": "UTC",
}


def resolve_timezone(name: str, default: ZoneInfo) -> ZoneInfo:
    name = name.strip()
    if not name:
        return default
    try:
        return ZoneInfo(TIMEZONE_ALIASES.get(name, name))
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"未知的時區：{name}（請使用 IANA 名稱，例如 Asia/Tokyo）") from error


def format_time(value: datetime, tz: ZoneInfo) -> str:
    return f"{value:%Y-%m-%d %H:%M:%S}（星期{WEEKDAYS[value.weekday()]}，{tz.key}）"


def now_text(tz: ZoneInfo) -> str:
    return format_time(datetime.now(tz), tz)


def build_time_tools(timezone: str) -> list[FunctionTool]:
    default_tz = ZoneInfo(timezone)

    @function_tool
    def get_current_time(timezone: str = "") -> str:
        """取得目前的日期、時間與星期。

        Args:
            timezone: 要查詢的時區或地名，例如 Asia/Tokyo、紐約；留空使用預設時區。
        """
        try:
            tz = resolve_timezone(timezone, default_tz)
        except ValueError as error:
            return str(error)
        return now_text(tz)

    @function_tool
    def convert_time(time: str, from_timezone: str, to_timezone: str) -> str:
        """將某個時區的時間換算為另一個時區，例如確認海外直播或賽事的當地時間。

        Args:
            time: 時間，格式 YYYY-MM-DD HH:MM。
            from_timezone: 原時區或地名，例如 America/New_York、東京。
            to_timezone: 目標時區或地名；留空使用預設時區。
        """
        try:
            source = resolve_timezone(from_timezone, default_tz)
            target = resolve_timezone(to_timezone, default_tz)
            value = datetime.strptime(time.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=source)
        except ValueError as error:
            return f"換算失敗：{error}"
        return f"{format_time(value, source)} = {format_time(value.astimezone(target), target)}"

    return [get_current_time, convert_time]
