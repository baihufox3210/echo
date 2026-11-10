"""搜尋用的文字比對：關鍵詞切分、相關度計分、縮寫比對、段落擷取。

全部為不需呼叫模型的確定性演算法，讓每次工具呼叫能多做一點事，
減少模型來回的次數。
"""

import re
from collections import Counter
from collections.abc import Iterable

CJK = r"㐀-䶿一-鿿぀-ヿ"
TOKEN_PATTERN = re.compile(rf"[{CJK}]+|[A-Za-z0-9][A-Za-z0-9+#.\-]*")
CJK_PATTERN = re.compile(rf"[{CJK}]")
# 網頁標題常見的分隔符號：「學程簡介 - 人工智慧與資訊安全學士學位學程 | 輔仁大學」
TITLE_SEPARATORS = re.compile(r"\s*[|｜\-–—_:：»›·•]\s+|\s+[|｜\-–—_:：»›·•]\s*|[|｜]")

STOPWORDS = {
    "的", "是", "了", "嗎", "呢", "和", "與", "及", "或", "在", "有", "什麼", "怎麼", "如何", "哪些",
    "the", "a", "an", "of", "and", "or", "to", "in", "is", "for", "what", "how",
}
# 不能當作「正式名稱」的常見標題片段
GENERIC_TITLES = {
    "首頁", "home", "最新消息", "公告", "新聞", "關於我們", "about", "index", "facebook", "instagram",
    "youtube", "wikipedia", "維基百科", "維基百科，自由的百科全書", "dcard", "ptt", "login", "登入",
}


def is_cjk(text: str) -> bool:
    return bool(CJK_PATTERN.search(text))


def query_terms(query: str) -> list[str]:
    """切出查詢中的關鍵詞（中文為連續字串，英數為單字），去除停用詞。"""
    terms: list[str] = []
    for token in TOKEN_PATTERN.findall(query):
        token = token.lower()
        if token in STOPWORDS or (len(token) == 1 and not is_cjk(token)):
            continue
        if token not in terms:
            terms.append(token)
    return terms


def bigrams(term: str) -> list[str]:
    return [term[i:i + 2] for i in range(len(term) - 1)] or [term]


def subsequence_coverage(short: str, long: str) -> float:
    """short 的字元依序出現在 long 中的比例（0~1）。

    用於比對中文縮寫：「輔大智慧資安」的每個字都依序出現在
    「輔仁大學人工智慧與資訊安全學士學位學程」中，因此為 1.0。
    """
    short = "".join(TOKEN_PATTERN.findall(short.lower()))
    long = long.lower()
    if not short:
        return 0.0
    position = 0
    matched = 0
    for char in short:
        found = long.find(char, position)
        if found != -1:
            matched += 1
            position = found + 1
    return matched / len(short)


def relevance(terms: list[str], text: str) -> float:
    """文字與查詢詞的相關度。完整出現得分最高，中文詞另以 bigram 計算部分符合。"""
    if not terms or not text:
        return 0.0
    haystack = text.lower()
    score = 0.0
    for term in terms:
        if term in haystack:
            score += 3.0 + min(haystack.count(term), 3) * 0.5
        elif is_cjk(term) and len(term) > 2:
            grams = bigrams(term)
            hit = sum(1 for gram in grams if gram in haystack)
            score += 2.0 * hit / len(grams)
    return score / len(terms)


def title_segments(title: str) -> list[str]:
    """把網頁標題依分隔符號切成片段。"""
    return [segment.strip(" 　\t「」『』【】[]()（）") for segment in TITLE_SEPARATORS.split(title) if segment.strip()]


def title_coverage(query: str, title: str) -> float:
    """查詢簡稱與整個標題的符合度；標題片段順序不定（「學程 - 學校」或「學校 - 學程」），兩種都試。"""
    segments = title_segments(title)
    return max(
        subsequence_coverage(query, title),
        subsequence_coverage(query, " ".join(reversed(segments))),
    )


def candidate_names(query: str, titles: Iterable[str], limit: int = 8) -> list[dict]:
    """從搜尋結果標題中找出查詢可能指涉的「正式名稱」。

    依縮寫符合度（查詢字元是否依序出現）與出現次數排序。
    """
    counts: Counter[str] = Counter()
    whole_coverage: dict[str, float] = {}
    for title in titles:
        whole = title_coverage(query, title)
        for segment in dict.fromkeys(title_segments(title)):
            if 2 <= len(segment) <= 40 and segment.lower() not in GENERIC_TITLES:
                counts[segment] += 1
                whole_coverage[segment] = max(whole_coverage.get(segment, 0.0), whole)

    compact_query = "".join(TOKEN_PATTERN.findall(query.lower()))
    scored = []
    for name, count in counts.items():
        # 簡稱常橫跨標題的多個片段（「輔大」在校名、「智慧資安」在學程名），
        # 因此同時參考片段本身與整個標題的符合度
        coverage = max(subsequence_coverage(query, name), 0.8 * whole_coverage[name])
        if coverage < 0.5 and count < 2:
            continue
        # 直接重複使用者說法的片段（多為論壇文章標題）不是正式名稱
        echo = compact_query in "".join(TOKEN_PATTERN.findall(name.lower()))
        scored.append((coverage + min(count, 4) * 0.1 - (0.5 if echo else 0), name, count, coverage))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {"name": name, "seen_in_results": count, "match": round(coverage, 2)}
        for _, name, count, coverage in scored[:limit]
    ]


def best_passages(text: str, terms: list[str], max_chars: int, context_lines: int = 1) -> str:
    """從長文中取出與查詢最相關的段落（含前後文），依原文順序排列。"""
    lines = text.splitlines()
    if not lines or not terms:
        return text[:max_chars]

    scores = [relevance(terms, line) for line in lines]
    ranked = sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: scores[i], reverse=True)
    if not ranked:
        return ""

    chosen: set[int] = set()
    used = 0
    for index in ranked:
        window = range(max(0, index - context_lines), min(len(lines), index + context_lines + 1))
        added = sum(len(lines[i]) + 1 for i in window if i not in chosen)
        if used + added > max_chars and chosen:
            break
        chosen.update(window)
        used += added

    parts: list[str] = []
    previous = None
    for index in sorted(chosen):
        if previous is not None and index != previous + 1:
            parts.append("…")
        parts.append(lines[index])
        previous = index
    return "\n".join(parts)[:max_chars]
