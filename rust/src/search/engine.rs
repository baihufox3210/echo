//! SearXNG 搜尋：查詢組合、結果整理、多查詢合併與模糊探索。

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use futures_util::future::join_all;
use serde::Serialize;
use serde_json::Value;

use super::common::{TtlCache, USER_AGENT, clean_domain, host_matches, hostname, normalize_url};
use super::text::{CandidateName, candidate_names, query_terms, relevance, title_coverage};
use crate::config::Settings;
use crate::text::truncate;

const SNIPPET_MAX_CHARS: usize = 400;
const MAX_PARALLEL_QUERIES: usize = 4;
const RRF_K: f64 = 60.0;
/// 探索階段額外嘗試的查詢後綴，用來找出官方網站與正式名稱
const DISCOVER_SUFFIXES: &[&str] = &["", "官網", "是什麼"];
const OFFICIAL_TLDS: &[&str] = &[".edu.tw", ".gov.tw", ".edu", ".gov", ".ac.jp", ".ac.uk", ".org.tw"];
/// 常見的非官方彙整／社群網站，不視為官方來源
const AGGREGATOR_DOMAINS: &[&str] = &[
    "facebook.com", "instagram.com", "youtube.com", "dcard.tw", "ptt.cc", "threads.net", "x.com", "twitter.com",
    "wikipedia.org", "104.com.tw", "1111.com.tw", "collego.edu.tw", "tiktok.com",
];
const DISCOVER_NEXT_STEPS: &str = "從 candidate_names 選出最符合的正式名稱，用 web_search 以正式名稱加上想查的細節再搜尋；\
若答案可能在官方網站內，用 explore_site 指定 sites 中的網址與想找的內容。";

#[derive(Debug, Clone, Default)]
pub struct SearchOptions {
    /// any／day／week／month／year
    pub time_range: String,
    /// general／news／science／it／videos
    pub category: String,
    pub language: String,
    pub page: u32,
    pub max_results: Option<usize>,
    /// 只保留此網域的結果（搜尋引擎不一定支援 site: 語法，因此在本地再過濾一次）
    pub site: String,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct SearchResult {
    pub title: String,
    pub url: String,
    pub snippet: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub published: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub official: Option<bool>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub matched_queries: Vec<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct Infobox {
    pub title: String,
    pub content: String,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct SearchPayload {
    #[serde(skip_serializing_if = "String::is_empty")]
    pub query: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub queries: Vec<String>,
    pub results: Vec<SearchResult>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub answers: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub infobox: Option<Infobox>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub suggestions: Vec<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub corrections: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

impl SearchPayload {
    fn error(message: impl Into<String>) -> Self {
        Self { error: Some(message.into()), ..Self::default() }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SiteCandidate {
    pub site: String,
    pub official: bool,
    pub pages: usize,
    /// 最接近首頁的網址，作為 explore_site 的進入點
    pub example: String,
    pub title: String,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct DiscoverPayload {
    pub topic: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub candidate_names: Vec<CandidateName>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub sites: Vec<SiteCandidate>,
    pub results: Vec<SearchResult>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub answers: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub next_steps: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// 組合搜尋引擎通用的進階語法。
pub fn build_query(query: &str, site: &str, exact_phrase: &str, filetype: &str) -> String {
    let mut parts = vec![query.trim().to_owned()];
    if !exact_phrase.trim().is_empty() {
        parts.push(format!("\"{}\"", exact_phrase.trim().replace('"', "")));
    }
    if !site.trim().is_empty() {
        parts.push(format!("site:{}", clean_domain(site)));
    }
    if !filetype.trim().is_empty() {
        parts.push(format!("filetype:{}", filetype.trim().trim_start_matches('.').to_lowercase()));
    }
    parts.retain(|part| !part.is_empty());
    parts.join(" ")
}

fn is_community(url: &str) -> bool {
    AGGREGATOR_DOMAINS.iter().any(|domain| host_matches(url, domain))
}

pub fn is_official(url: &str) -> bool {
    let host = hostname(url);
    OFFICIAL_TLDS.iter().any(|tld| host.ends_with(tld)) && !is_community(url)
}

pub struct SearchEngine {
    settings: Arc<Settings>,
    base_url: Option<String>,
    client: reqwest::Client,
    cache: TtlCache<String, SearchPayload>,
}

impl SearchEngine {
    pub fn new(settings: Arc<Settings>) -> Self {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs_f64(settings.search_timeout))
            .user_agent(USER_AGENT)
            .build()
            .expect("建立搜尋用的 HTTP 客戶端失敗");
        Self {
            base_url: settings.searxng_url.as_ref().map(|url| url.trim_end_matches('/').to_owned()),
            cache: TtlCache::new(Duration::from_secs_f64(settings.search_cache_ttl)),
            client,
            settings,
        }
    }

    pub fn enabled(&self) -> bool {
        self.base_url.is_some()
    }

    /// 搜尋並回傳整理過的結果；`query` 可包含進階語法。
    pub async fn search(&self, query: &str, options: &SearchOptions) -> SearchPayload {
        let query = query.trim();
        if query.is_empty() {
            return SearchPayload::error("搜尋關鍵字不可為空");
        }
        let Some(base_url) = &self.base_url else {
            return SearchPayload::error("網路搜尋功能未啟用");
        };

        let limit = options.max_results.unwrap_or(self.settings.search_max_results).min(20);
        let page = options.page.clamp(1, 5);
        let language = if options.language.trim().is_empty() { &self.settings.search_language } else { options.language.trim() };
        let time_range = if options.time_range.is_empty() { "any" } else { &options.time_range };
        let category = if options.category.is_empty() { "general" } else { &options.category };

        let cache_key = format!("{query}\u{1}{time_range}\u{1}{category}\u{1}{language}\u{1}{page}\u{1}{limit}\u{1}{}", options.site);
        if let Some(cached) = self.cache.get(&cache_key) {
            return cached;
        }

        let mut params = vec![
            ("q", query.to_owned()),
            ("format", "json".to_owned()),
            ("language", language.to_owned()),
            ("categories", category.to_owned()),
            ("safesearch", "1".to_owned()),
            ("pageno", page.to_string()),
        ];
        if time_range != "any" {
            params.push(("time_range", time_range.to_owned()));
        }

        let data = match self.request(base_url, &params).await {
            Ok(data) => data,
            Err(reason) => {
                tracing::warn!(query, reason, "web search failed");
                return SearchPayload::error(format!("搜尋失敗：{reason}"));
            }
        };

        let payload = format_results(query, &data, limit, &options.site);
        if !payload.results.is_empty() || !payload.answers.is_empty() {
            self.cache.set(cache_key, payload.clone());
        }
        tracing::info!(query, results = payload.results.len(), "web search");
        payload
    }

    /// 並行執行多個查詢，以 Reciprocal Rank Fusion 合併排序。
    pub async fn search_many(&self, queries: &[String], options: &SearchOptions) -> SearchPayload {
        let mut unique: Vec<String> = Vec::new();
        for query in queries.iter().map(|q| q.trim()).filter(|q| !q.is_empty()) {
            if !unique.iter().any(|existing| existing == query) {
                unique.push(query.to_owned());
            }
        }
        unique.truncate(MAX_PARALLEL_QUERIES);
        match unique.len() {
            0 => return SearchPayload::error("至少需要一個查詢"),
            1 => return self.search(&unique[0], options).await,
            _ => {}
        }

        let each = SearchOptions { max_results: Some(10), ..options.clone() };
        let payloads = join_all(unique.iter().map(|query| self.search(query, &each))).await;

        let mut order: Vec<String> = Vec::new();
        let mut scores: HashMap<String, f64> = HashMap::new();
        let mut entries: HashMap<String, SearchResult> = HashMap::new();
        let mut answers: Vec<String> = Vec::new();
        for payload in &payloads {
            for answer in &payload.answers {
                if !answers.contains(answer) {
                    answers.push(answer.clone());
                }
            }
            for (rank, entry) in payload.results.iter().enumerate() {
                let key = normalize_url(&entry.url);
                *scores.entry(key.clone()).or_default() += 1.0 / (RRF_K + rank as f64 + 1.0);
                let merged = entries.entry(key.clone()).or_insert_with(|| {
                    order.push(key);
                    entry.clone()
                });
                merged.matched_queries.push(payload.query.clone());
            }
        }

        // 穩定排序：分數相同時維持首次出現的順序
        order.sort_by(|a, b| scores[b].total_cmp(&scores[a]));
        let limit = options.max_results.unwrap_or(self.settings.search_max_results + 3);
        let results: Vec<SearchResult> = order.iter().take(limit).filter_map(|key| entries.remove(key)).collect();

        answers.truncate(3);
        let error = if results.is_empty() { payloads.iter().find_map(|p| p.error.clone()) } else { None };
        SearchPayload { queries: unique, results, answers, error, ..SearchPayload::default() }
    }

    /// 第一階段的模糊探索：找出主題的正式名稱、官方網站與相關條目。
    ///
    /// 使用者常用簡稱或不精確的說法（例如「輔大智慧資安」），直接搜尋細節容易失準。
    /// 這裡同時以數種說法搜尋，從結果標題歸納出可能的正式名稱，
    /// 並標出官方網域，供下一步精確搜尋或站內查找使用。
    pub async fn discover(&self, topic: &str) -> DiscoverPayload {
        let topic = topic.trim();
        if topic.is_empty() {
            return DiscoverPayload { error: Some("主題不可為空".into()), ..DiscoverPayload::default() };
        }

        let queries: Vec<String> =
            DISCOVER_SUFFIXES.iter().map(|suffix| format!("{topic} {suffix}").trim().to_owned()).collect();
        let merged = self.search_many(&queries, &SearchOptions { max_results: Some(12), ..SearchOptions::default() }).await;
        if merged.results.is_empty() {
            return DiscoverPayload { topic: topic.to_owned(), note: merged.note, error: merged.error, ..DiscoverPayload::default() };
        }

        let terms = query_terms(topic);
        let mut scored: Vec<(f64, SearchResult)> = merged
            .results
            .into_iter()
            .map(|mut entry| {
                let official = is_official(&entry.url);
                // 探索階段以辨識對象為主：官方來源優先，論壇與社群貼文靠後
                let score = relevance(&terms, &format!("{} {}", entry.title, entry.snippet))
                    + 2.0 * title_coverage(topic, &entry.title)
                    + if official { 2.5 } else { 0.0 }
                    - if is_community(&entry.url) { 1.5 } else { 0.0 };
                entry.official = Some(official);
                entry.matched_queries.clear();
                (score, entry)
            })
            .collect();
        scored.sort_by(|a, b| b.0.total_cmp(&a.0));
        let results: Vec<SearchResult> = scored.into_iter().map(|(_, entry)| entry).collect();

        let mut sites: Vec<SiteCandidate> = Vec::new();
        for entry in results.iter().filter(|entry| !is_community(&entry.url)) {
            let host = hostname(&entry.url);
            match sites.iter_mut().find(|site| site.site == host) {
                Some(site) => {
                    site.pages += 1;
                    if entry.url.len() < site.example.len() {
                        site.example = entry.url.clone();
                        site.title = entry.title.clone();
                    }
                }
                None => sites.push(SiteCandidate {
                    site: host,
                    official: entry.official.unwrap_or(false),
                    pages: 1,
                    example: entry.url.clone(),
                    title: entry.title.clone(),
                }),
            }
        }
        sites.sort_by_key(|site| std::cmp::Reverse((site.official, site.pages)));
        sites.truncate(6);

        DiscoverPayload {
            topic: topic.to_owned(),
            candidate_names: candidate_names(topic, results.iter().map(|entry| entry.title.as_str()), 8),
            sites,
            results: results.into_iter().take(8).collect(),
            answers: merged.answers,
            next_steps: Some(DISCOVER_NEXT_STEPS.to_owned()),
            note: None,
            error: None,
        }
    }

    /// 查詢 SearXNG；遇到連線錯誤、429 或 5xx 時重試一次。
    async fn request(&self, base_url: &str, params: &[(&str, String)]) -> Result<Value, String> {
        const ATTEMPTS: usize = 2;
        let mut last_error = String::new();
        for attempt in 0..ATTEMPTS {
            if attempt > 0 {
                tokio::time::sleep(Duration::from_millis(500)).await;
            }
            match self.client.get(format!("{base_url}/search")).query(params).send().await {
                Ok(response) => {
                    let status = response.status();
                    if status.is_success() {
                        return response.json().await.map_err(|_| "回應不是有效的 JSON".to_owned());
                    }
                    last_error = format!("HTTP {}", status.as_u16());
                    if !(status.is_server_error() || status.as_u16() == 429) {
                        break;
                    }
                }
                Err(error) => last_error = if error.is_timeout() { "逾時".to_owned() } else { "連線失敗".to_owned() },
            }
        }
        Err(last_error)
    }
}

fn text_of(value: &Value, key: &str) -> String {
    value.get(key).and_then(Value::as_str).unwrap_or("").to_owned()
}

fn string_list(value: Option<&Value>, limit: usize) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .map(|items| items.iter().filter_map(Value::as_str).map(str::to_owned).take(limit).collect())
        .unwrap_or_default()
}

fn format_results(query: &str, data: &Value, limit: usize, site: &str) -> SearchPayload {
    let site = if site.trim().is_empty() { String::new() } else { clean_domain(site) };
    let mut results: Vec<SearchResult> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    let mut filtered_out = 0usize;

    for item in data.get("results").and_then(Value::as_array).into_iter().flatten() {
        let url = text_of(item, "url");
        if !(url.starts_with("http://") || url.starts_with("https://")) {
            continue;
        }
        let key = normalize_url(&url);
        if seen.contains(&key) {
            continue;
        }
        seen.push(key);

        if !site.is_empty() && !host_matches(&url, &site) {
            filtered_out += 1;
            continue;
        }
        results.push(SearchResult {
            title: truncate(&text_of(item, "title"), 200),
            snippet: truncate(&text_of(item, "content"), SNIPPET_MAX_CHARS),
            published: item.get("publishedDate").and_then(Value::as_str).map(|date| date.chars().take(10).collect()),
            url,
            ..SearchResult::default()
        });
        if results.len() >= limit {
            break;
        }
    }

    let answers: Vec<String> = data
        .get("answers")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|answer| match answer {
            Value::String(text) => text.clone(),
            other => text_of(other, "answer"),
        })
        .map(|answer| truncate(&answer, SNIPPET_MAX_CHARS))
        .filter(|answer| !answer.is_empty())
        .take(3)
        .collect();

    let infobox = data
        .get("infoboxes")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .find(|item| !text_of(item, "content").is_empty())
        .map(|item| Infobox { title: text_of(item, "infobox"), content: truncate(&text_of(item, "content"), 800) });

    let empty = results.is_empty();
    let unresponsive = data.get("unresponsive_engines").and_then(Value::as_array).is_some_and(|engines| !engines.is_empty());
    let note = if !empty {
        None
    } else if filtered_out > 0 {
        Some(format!("有 {filtered_out} 筆結果因網域條件被排除，可放寬 site 條件再試。"))
    } else if unresponsive {
        Some("部分搜尋引擎暫時無回應，可稍後重試或更換關鍵字。".to_owned())
    } else {
        None
    };

    SearchPayload {
        query: query.to_owned(),
        results,
        answers,
        infobox,
        suggestions: if empty { string_list(data.get("suggestions"), 5) } else { Vec::new() },
        corrections: if empty { string_list(data.get("corrections"), 3) } else { Vec::new() },
        note,
        ..SearchPayload::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_query_with_advanced_operators() {
        assert_eq!(
            build_query("python release", "https://www.python.org/downloads", "3.15", ".PDF"),
            "python release \"3.15\" site:python.org filetype:pdf"
        );
        assert_eq!(build_query("  plain  ", "", "", ""), "plain");
    }

    #[test]
    fn official_and_community_sites() {
        assert!(is_official("https://ais.fju.edu.tw/"));
        assert!(!is_official("https://collego.edu.tw/x"));
        assert!(!is_official("https://www.dcard.tw/f"));
    }
}
