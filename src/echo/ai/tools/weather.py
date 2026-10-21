import json

from agents import FunctionTool, RunContextWrapper, function_tool

from echo.ai.context import BUDGET_EXHAUSTED, AgentEvent, RunContext
from echo.ai.tools.http import ApiClient, ApiError

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WEATHER_CODES = {
    0: "晴朗", 1: "大致晴朗", 2: "局部多雲", 3: "陰天", 45: "起霧", 48: "霧凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 56: "凍毛毛雨", 57: "強凍毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "凍雨", 67: "強凍雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "短暫陣雨", 81: "陣雨", 82: "強陣雨", 85: "陣雪", 86: "強陣雪",
    95: "雷雨", 96: "雷雨伴隨冰雹", 99: "強雷雨伴隨冰雹",
}


def describe(code: int | None) -> str:
    return WEATHER_CODES.get(code, "未知") if code is not None else "未知"


async def lookup_weather(api: ApiClient, location: str, days: int) -> dict:
    places = await api.get_json(GEOCODING_URL, {"name": location, "count": 1, "language": "zh", "format": "json"})
    results = (places or {}).get("results") or []
    if not results:
        return {"error": f"找不到地點「{location}」，可改用英文名稱或更大的行政區再試"}
    place = results[0]

    forecast = await api.get_json(FORECAST_URL, {
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,uv_index_max",
        "timezone": "auto",
        "forecast_days": days,
    })
    current = forecast.get("current", {})
    daily = forecast.get("daily", {})

    return {
        "location": ", ".join(filter(None, (place.get("name"), place.get("admin1"), place.get("country")))),
        "timezone": forecast.get("timezone"),
        "current": {
            "time": current.get("time"),
            "weather": describe(current.get("weather_code")),
            "temperature_c": current.get("temperature_2m"),
            "feels_like_c": current.get("apparent_temperature"),
            "humidity_percent": current.get("relative_humidity_2m"),
            "precipitation_mm": current.get("precipitation"),
            "wind_kmh": current.get("wind_speed_10m"),
        },
        "forecast": [
            {
                "date": date,
                "weather": describe(daily["weather_code"][i]),
                "min_c": daily["temperature_2m_min"][i],
                "max_c": daily["temperature_2m_max"][i],
                "rain_chance_percent": daily["precipitation_probability_max"][i],
                "uv_index": daily["uv_index_max"][i],
            }
            for i, date in enumerate(daily.get("time", []))
        ],
        "source": "Open-Meteo",
    }


def build_weather_tool(api: ApiClient) -> FunctionTool:
    @function_tool
    async def get_weather(ctx: RunContextWrapper[RunContext], location: str, days: int = 3) -> str:
        """查詢指定地點目前的天氣與未來幾天的預報（氣溫、降雨機率、紫外線等）。

        Args:
            location: 城市或地區名稱，例如「台北」、「Tokyo」。找不到時可改用英文名稱。
            days: 預報天數 1~7。
        """
        context = ctx.context
        if not context.consume_budget():
            return BUDGET_EXHAUSTED

        await context.emit(AgentEvent("tool", f"🌤️ 查詢{location}的天氣"))
        try:
            result = await lookup_weather(api, location, min(max(days, 1), 7))
        except (ApiError, KeyError, IndexError, TypeError):
            return json.dumps({"error": "天氣服務暫時無法使用"}, ensure_ascii=False)
        return json.dumps(result, ensure_ascii=False)

    return get_weather
