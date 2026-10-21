import json
from urllib.parse import quote

from agents import FunctionTool, RunContextWrapper, function_tool

from echo.ai.context import BUDGET_EXHAUSTED, AgentEvent, RunContext
from echo.ai.tools.http import ApiClient, ApiError

EXCHANGE_URL = "https://open.er-api.com/v6/latest/{base}"
WIKI_LANG_HEADERS = {"zh": {"Accept-Language": "zh-tw"}}


async def wikipedia_lookup(api: ApiClient, query: str, lang: str) -> dict:
    lang = lang if lang.isalpha() and len(lang) <= 3 else "zh"
    headers = WIKI_LANG_HEADERS.get(lang)
    search = await api.get_json(
        f"https://{lang}.wikipedia.org/w/rest.php/v1/search/page",
        {"q": query, "limit": 3},
        headers,
    )
    pages = (search or {}).get("pages") or []
    if not pages:
        return {"error": f"維基百科找不到「{query}」的條目"}

    summary = await api.get_json(
        f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(pages[0]['key'], safe='')}",
        headers=headers,
    ) or {}
    return {
        "title": summary.get("title") or pages[0].get("title"),
        "description": summary.get("description", ""),
        "summary": summary.get("extract", ""),
        "url": summary.get("content_urls", {}).get("desktop", {}).get("page")
        or f"https://{lang}.wikipedia.org/wiki/{quote(pages[0]['key'])}",
        "other_matches": [p.get("title") for p in pages[1:]],
    }


async def convert(api: ApiClient, amount: float, from_currency: str, to_currency: str) -> dict:
    base, target = from_currency.strip().upper(), to_currency.strip().upper()
    data = await api.get_json(EXCHANGE_URL.format(base=quote(base)))
    if not data or data.get("result") != "success":
        return {"error": f"不支援的貨幣代碼：{base}"}
    rate = data.get("rates", {}).get(target)
    if rate is None:
        return {"error": f"不支援的貨幣代碼：{target}"}
    return {
        "amount": amount,
        "from": base,
        "to": target,
        "rate": rate,
        "result": round(amount * rate, 4),
        "updated": data.get("time_last_update_utc"),
        "source": "ExchangeRate-API",
    }


def build_knowledge_tools(api: ApiClient) -> list[FunctionTool]:
    @function_tool
    async def wikipedia(ctx: RunContextWrapper[RunContext], query: str, lang: str = "zh") -> str:
        """查詢維基百科條目摘要。適合人物、地點、作品、歷史、科學概念等百科知識。

        Args:
            query: 條目名稱或關鍵字。
            lang: 維基百科語言代碼，例如 zh、en、ja。中文條目不足時可改用 en。
        """
        context = ctx.context
        if not context.consume_budget():
            return BUDGET_EXHAUSTED

        await context.emit(AgentEvent("tool", f"▹ 查閱維基百科「{query[:40]}」"))
        try:
            result = await wikipedia_lookup(api, query, lang.strip().lower() or "zh")
        except (ApiError, KeyError, TypeError):
            return json.dumps({"error": "維基百科暫時無法使用"}, ensure_ascii=False)

        if url := result.get("url"):
            context.seen_urls.add(url)
        return context.guard.wrap("wikipedia", json.dumps(result, ensure_ascii=False))

    @function_tool
    async def convert_currency(
        ctx: RunContextWrapper[RunContext],
        amount: float,
        from_currency: str,
        to_currency: str,
    ) -> str:
        """以最新匯率換算貨幣金額。

        Args:
            amount: 金額。
            from_currency: 原幣別的 ISO 代碼，例如 TWD、USD、JPY。
            to_currency: 目標幣別的 ISO 代碼。
        """
        context = ctx.context
        if not context.consume_budget():
            return BUDGET_EXHAUSTED

        await context.emit(AgentEvent("tool", f"▹ 換算 {from_currency.upper()} → {to_currency.upper()}"))
        try:
            result = await convert(api, amount, from_currency, to_currency)
        except ApiError:
            result = {"error": "匯率服務暫時無法使用"}
        return json.dumps(result, ensure_ascii=False)

    return [wikipedia, convert_currency]
