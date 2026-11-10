//! 搜尋用的文字比對：關鍵詞切分、相關度計分、縮寫比對、段落擷取。
//!
//! 全部為不需呼叫模型的確定性演算法，讓每次工具呼叫能多做一點事，
//! 減少模型來回的次數。

use std::collections::{BTreeSet, HashMap};
use std::sync::LazyLock;

use regex::Regex;
use serde::Serialize;

const CJK: &str = r"\u{3400}-\u{4dbf}\u{4e00}-\u{9fff}\u{3040}-\u{30ff}";

static TOKEN_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(&format!(r"[{CJK}]+|[A-Za-z0-9][A-Za-z0-9+#.\-]*")).unwrap());
static CJK_PATTERN: LazyLock<Regex> = LazyLock::new(|| Regex::new(&format!("[{CJK}]")).unwrap());
/// 網頁標題常見的分隔符號：「學程簡介 - 人工智慧與資訊安全學士學位學程 | 輔仁大學」
static TITLE_SEPARATORS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\s*[|｜\-–—_:：»›·•]\s+|\s+[|｜\-–—_:：»›·•]\s*|[|｜]").unwrap()
});

const STOPWORDS: &[&str] = &[
    "的", "是", "了", "嗎", "呢", "和", "與", "及", "或", "在", "有", "什麼", "怎麼", "如何", "哪些",
    "the", "a", "an", "of", "and", "or", "to", "in", "is", "for", "what", "how",
];
/// 不能當作「正式名稱」的常見標題片段
const GENERIC_TITLES: &[&str] = &[
    "首頁", "home", "最新消息", "公告", "新聞", "關於我們", "about", "index", "facebook", "instagram",
    "youtube", "wikipedia", "維基百科", "維基百科，自由的百科全書", "dcard", "ptt", "login", "登入",
];
const SEGMENT_TRIM: &[char] = &[' ', '\u{3000}', '\t', '「', '」', '『', '』', '【', '】', '[', ']', '(', ')', '（', '）'];

pub fn is_cjk(text: &str) -> bool {
    CJK_PATTERN.is_match(text)
}

/// 只保留可比對的字元（中文與英數），轉為小寫。
fn compact(text: &str) -> String {
    TOKEN_PATTERN.find_iter(&text.to_lowercase()).map(|m| m.as_str()).collect()
}

/// 切出查詢中的關鍵詞（中文為連續字串，英數為單字），去除停用詞。
pub fn query_terms(query: &str) -> Vec<String> {
    let mut terms: Vec<String> = Vec::new();
    for token in TOKEN_PATTERN.find_iter(query) {
        let token = token.as_str().to_lowercase();
        let single_latin = token.chars().count() == 1 && !is_cjk(&token);
        if STOPWORDS.contains(&token.as_str()) || single_latin || terms.contains(&token) {
            continue;
        }
        terms.push(token);
    }
    terms
}

fn bigrams(term: &str) -> Vec<String> {
    let chars: Vec<char> = term.chars().collect();
    if chars.len() < 2 {
        return vec![term.to_owned()];
    }
    chars.windows(2).map(|pair| pair.iter().collect()).collect()
}

/// `short` 的字元依序出現在 `long` 中的比例（0～1）。
///
/// 用於比對中文縮寫：「輔大智慧資安」的每個字都依序出現在
/// 「輔仁大學人工智慧與資訊安全學士學位學程」中，因此為 1.0。
pub fn subsequence_coverage(short: &str, long: &str) -> f64 {
    let short = compact(short);
    if short.is_empty() {
        return 0.0;
    }
    let long: Vec<char> = long.to_lowercase().chars().collect();
    let mut position = 0;
    let mut matched = 0usize;
    for target in short.chars() {
        if let Some(offset) = long[position..].iter().position(|c| *c == target) {
            matched += 1;
            position += offset + 1;
        }
    }
    matched as f64 / short.chars().count() as f64
}

/// 文字與查詢詞的相關度。完整出現得分最高，中文詞另以 bigram 計算部分符合。
pub fn relevance(terms: &[String], text: &str) -> f64 {
    if terms.is_empty() || text.is_empty() {
        return 0.0;
    }
    let haystack = text.to_lowercase();
    let mut score = 0.0;
    for term in terms {
        if haystack.contains(term.as_str()) {
            score += 3.0 + haystack.matches(term.as_str()).count().min(3) as f64 * 0.5;
        } else if is_cjk(term) && term.chars().count() > 2 {
            let grams = bigrams(term);
            let hit = grams.iter().filter(|gram| haystack.contains(gram.as_str())).count();
            score += 2.0 * hit as f64 / grams.len() as f64;
        }
    }
    score / terms.len() as f64
}

/// 把網頁標題依分隔符號切成片段。
pub fn title_segments(title: &str) -> Vec<String> {
    TITLE_SEPARATORS
        .split(title)
        .filter(|segment| !segment.trim().is_empty())
        .map(|segment| segment.trim_matches(SEGMENT_TRIM).to_owned())
        .collect()
}

/// 查詢簡稱與整個標題的符合度；標題片段順序不定（「學程 - 學校」或「學校 - 學程」），兩種都試。
pub fn title_coverage(query: &str, title: &str) -> f64 {
    let mut segments = title_segments(title);
    segments.reverse();
    subsequence_coverage(query, title).max(subsequence_coverage(query, &segments.join(" ")))
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct CandidateName {
    pub name: String,
    pub seen_in_results: usize,
    /// 與查詢的符合度（0～1，取到小數兩位）
    #[serde(rename = "match")]
    pub coverage: f64,
}

/// 從搜尋結果標題中找出查詢可能指涉的「正式名稱」，
/// 依縮寫符合度（查詢字元是否依序出現）與出現次數排序。
pub fn candidate_names<'a>(query: &str, titles: impl IntoIterator<Item = &'a str>, limit: usize) -> Vec<CandidateName> {
    // 以 Vec 保留首次出現的順序，分數相同時結果穩定
    let mut order: Vec<String> = Vec::new();
    let mut counts: HashMap<String, usize> = HashMap::new();
    let mut whole_coverage: HashMap<String, f64> = HashMap::new();

    for title in titles {
        let whole = title_coverage(query, title);
        let mut seen_in_title: Vec<String> = Vec::new();
        for segment in title_segments(title) {
            let length = segment.chars().count();
            if !(2..=40).contains(&length)
                || GENERIC_TITLES.contains(&segment.to_lowercase().as_str())
                || seen_in_title.contains(&segment)
            {
                continue;
            }
            seen_in_title.push(segment.clone());
            if !counts.contains_key(&segment) {
                order.push(segment.clone());
            }
            *counts.entry(segment.clone()).or_default() += 1;
            let best = whole_coverage.entry(segment).or_default();
            *best = best.max(whole);
        }
    }

    let compact_query = compact(query);
    let mut scored: Vec<(f64, CandidateName)> = Vec::new();
    for name in order {
        let count = counts[&name];
        // 簡稱常橫跨標題的多個片段（「輔大」在校名、「智慧資安」在學程名），
        // 因此同時參考片段本身與整個標題的符合度
        let coverage = subsequence_coverage(query, &name).max(0.8 * whole_coverage[&name]);
        if coverage < 0.5 && count < 2 {
            continue;
        }
        // 直接重複使用者說法的片段（多為論壇文章標題）不是正式名稱
        let echo = !compact_query.is_empty() && compact(&name).contains(&compact_query);
        let score = coverage + count.min(4) as f64 * 0.1 - if echo { 0.5 } else { 0.0 };
        scored.push((
            score,
            CandidateName { name, seen_in_results: count, coverage: (coverage * 100.0).round() / 100.0 },
        ));
    }

    scored.sort_by(|a, b| b.0.total_cmp(&a.0));
    scored.into_iter().take(limit).map(|(_, candidate)| candidate).collect()
}

/// 從長文中取出與查詢最相關的段落（含前後文），依原文順序排列；長度以字元計。
pub fn best_passages(text: &str, terms: &[String], max_chars: usize, context_lines: usize) -> String {
    let lines: Vec<&str> = text.lines().collect();
    if lines.is_empty() || terms.is_empty() {
        return text.chars().take(max_chars).collect();
    }

    let scores: Vec<f64> = lines.iter().map(|line| relevance(terms, line)).collect();
    let mut ranked: Vec<usize> = (0..lines.len()).filter(|i| scores[*i] > 0.0).collect();
    if ranked.is_empty() {
        return String::new();
    }
    ranked.sort_by(|a, b| scores[*b].total_cmp(&scores[*a]));

    let mut chosen: BTreeSet<usize> = BTreeSet::new();
    let mut used = 0usize;
    for index in ranked {
        let window = index.saturating_sub(context_lines)..(index + context_lines + 1).min(lines.len());
        let added: usize = window.clone().filter(|i| !chosen.contains(i)).map(|i| lines[i].chars().count() + 1).sum();
        if used + added > max_chars && !chosen.is_empty() {
            break;
        }
        chosen.extend(window);
        used += added;
    }

    let mut parts: Vec<&str> = Vec::new();
    let mut previous: Option<usize> = None;
    for index in chosen {
        if previous.is_some_and(|p| index != p + 1) {
            parts.push("…");
        }
        parts.push(lines[index]);
        previous = Some(index);
    }
    parts.join("\n").chars().take(max_chars).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const FORMAL_NAME: &str = "人工智慧與資訊安全學士學位學程";

    #[test]
    fn abbreviation_matches_formal_name() {
        assert_eq!(subsequence_coverage("輔大智慧資安", &format!("輔仁大學{FORMAL_NAME}")), 1.0);
        assert!(subsequence_coverage("輔大智慧資安", "台灣大學資訊工程學系") < 0.5);
        // 標題片段順序相反時也能比對
        assert_eq!(title_coverage("輔大智慧資安", &format!("{FORMAL_NAME} - 輔仁大學")), 1.0);
    }

    #[test]
    fn candidate_names_finds_formal_name_from_titles() {
        let titles = [
            format!("{FORMAL_NAME} - 輔仁大學"),
            format!("學程簡介 | {FORMAL_NAME}"),
            "輔大智慧資安學程好嗎 - Dcard".to_owned(),
            "首頁 - 某補習班".to_owned(),
        ];
        let names = candidate_names("輔大智慧資安", titles.iter().map(String::as_str), 8);

        assert_eq!(names[0].name, FORMAL_NAME);
        assert_eq!(names[0].seen_in_results, 2);
        assert!(names.iter().all(|n| n.name != "首頁"));
        // 直接重複使用者說法的論壇標題排在正式名稱之後
        let echo = names.iter().position(|n| n.name == "輔大智慧資安學程好嗎").unwrap();
        assert!(echo > 0);
    }

    #[test]
    fn query_terms_and_relevance() {
        let terms = query_terms("必修課程 的 學分");
        assert_eq!(terms, vec!["必修課程", "學分"]);

        let full = relevance(&terms, "本學程必修課程共 60 學分");
        let partial = relevance(&terms, "必修科目一覽");
        assert!(full > partial && partial > 0.0);
        assert_eq!(relevance(&terms, "交通資訊"), 0.0);
    }

    #[test]
    fn best_passages_keeps_context_and_drops_unrelated_lines() {
        let terms = query_terms("必修課程 學分");
        let text = ["交通資訊", "校園地圖", "畢業門檻", "必修課程共 60 學分", "含專題實作", "聯絡我們"].join("\n");
        let passages = best_passages(&text, &terms, 200, 1);

        assert!(passages.contains("必修課程共 60 學分") && passages.contains("含專題實作"));
        assert!(!passages.contains("交通資訊"));
        assert_eq!(best_passages(&text, &query_terms("宿舍"), 200, 1), "");
    }

    #[test]
    fn candidate_name_serializes_like_python_version() {
        let candidate = CandidateName { name: "x".into(), seen_in_results: 2, coverage: 0.67 };
        assert_eq!(
            serde_json::to_string(&candidate).unwrap(),
            r#"{"name":"x","seen_in_results":2,"match":0.67}"#
        );
    }
}
