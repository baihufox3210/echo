//! 站內查找：在單一網站內找出與問題相關的頁面並擷取段落。
//!
//! 很多資訊（課程地圖、師資、修業規定、招生簡章）不會出現在搜尋引擎的前幾筆結果，
//! 而是藏在系所網站的選單或附件裡。這裡綜合三種線索找出候選頁面：
//!   1. 起始頁面上的連結（包含導覽選單）
//!   2. 網站的 sitemap
//!   3. 搜尋引擎的站內搜尋（site:）
//!
//! 依連結文字／網址與問題的相關度排序後，實際讀取最相關的幾頁（最多兩層），
//! 再從每頁取出與問題最相關的段落。

use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use futures_util::StreamExt;
use futures_util::stream;
use serde::Serialize;
use url::Url;

use super::common::{hostname, normalize_url, same_site};
use super::engine::{SearchEngine, SearchOptions};
use super::fetcher::{FetchedPage, PageFetcher};
use super::text::{best_passages, query_terms, relevance};
use crate::config::Settings;
use crate::documents::Link;
use crate::text::truncate;

pub const DEFAULT_MAX_PAGES: usize = 6;
const HARD_MAX_PAGES: usize = 10;
const FIRST_ROUND: usize = 4;
const CONCURRENCY: usize = 3;
const MIN_PASSAGE_CHARS: usize = 1200;
const PAGE_SCORE_CHARS: usize = 30_000;
const SKIPPED_EXTENSIONS: &[&str] = &[
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".css", ".js", ".zip", ".rar", ".7z", ".mp3", ".mp4",
    ".avi", ".mov", ".ppt", ".pptx", ".xls", ".xlsx", ".exe", ".woff", ".woff2",
];
const SKIPPED_PATH_WORDS: &[&str] = &["login", "logout", "signin", "signup", "register", "wp-admin", "javascript:"];

/// 想找的細節與網站欄目名稱常常字面不同（找「必修科目」要先進「課程資訊」）。
/// 查詢包含左邊任一詞時，也把右邊的欄目用詞當作較弱的線索，用來挑選要進入的欄目頁。
const SECTION_HINTS: &[(&[&str], &[&str])] = &[
    (
        &["必修", "選修", "學分", "科目", "課表", "修課", "課綱", "課程", "學程", "curriculum", "course"],
        &["課程", "修業", "學程", "課表", "地圖", "course", "curriculum"],
    ),
    (&["教授", "老師", "教師", "師資", "專長", "faculty", "professor"], &["師資", "教師", "成員", "faculty", "people"]),
    (&["招生", "報名", "簡章", "名額", "入學", "甄試", "申請入學", "admission"], &["招生", "入學", "admission"]),
    (&["畢業", "門檻", "規定", "辦法", "法規", "修業", "規則"], &["修業", "法規", "規章", "辦法", "畢業", "表單", "下載"]),
    (&["獎學金", "補助", "學費", "scholarship"], &["獎助", "獎學金", "學務", "scholarship"]),
    (&["公告", "消息", "活動", "演講", "news"], &["公告", "消息", "活動", "news"]),
    (&["電話", "地址", "聯絡", "信箱", "位置", "contact"], &["聯絡", "關於", "簡介", "contact", "about"]),
    (&["簡介", "特色", "目標", "介紹", "沿革", "about"], &["簡介", "關於", "介紹", "特色", "about"]),
];
const HINT_WEIGHT: f64 = 0.6;
const NOTHING_FOUND_NOTE: &str = "站內沒有找到與查詢直接相符的內容。可參考 site_menu 的頁面名稱，\
改用網站上實際使用的詞彙再查一次，或用 web_fetch 直接閱讀其中的頁面。\
若網站內容由 JavaScript 動態載入，則無法讀取。";

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct PageExcerpt {
    pub url: String,
    pub title: String,
    pub passages: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct LinkRef {
    pub text: String,
    pub url: String,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct SiteReport {
    pub site: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub query: String,
    pub pages_checked: usize,
    pub pages_read: Vec<PageExcerpt>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub other_relevant_links: Vec<LinkRef>,
    /// 找不到相關內容時回報網站選單，讓模型自行判斷該看哪一頁或換個說法
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub site_menu: Vec<LinkRef>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Debug, Clone)]
struct Candidate {
    url: String,
    text: String,
    score: f64,
}

/// 把網址路徑轉成可比對的文字（解碼中文、分隔符號轉空白）。
fn url_words(url: &str) -> String {
    let Ok(parsed) = Url::parse(url) else { return String::new() };
    let raw = format!("{} {}", parsed.path(), parsed.query().unwrap_or(""));
    let decoded: String = url::form_urlencoded::parse(raw.replace('+', "%2B").as_bytes())
        .map(|(key, value)| format!("{key} {value}"))
        .collect::<Vec<_>>()
        .join(" ");
    decoded.chars().map(|c| if "/-_=&.+".contains(c) { ' ' } else { c }).collect()
}

fn section_hints(query: &str) -> Vec<&'static str> {
    let lowered = query.to_lowercase();
    let mut hints: Vec<&'static str> = Vec::new();
    for (triggers, sections) in SECTION_HINTS {
        if triggers.iter().any(|trigger| lowered.contains(trigger)) {
            hints.extend(sections.iter().filter(|word| !hints.contains(word)).copied().collect::<Vec<_>>());
        }
    }
    hints
}

/// 是否為值得自動讀取的頁面（排除圖片、壓縮檔、登入頁等）。
pub fn crawlable(url: &str) -> bool {
    let lowered = url.to_lowercase();
    let path = Url::parse(&lowered).map(|u| u.path().to_owned()).unwrap_or_default();
    !SKIPPED_EXTENSIONS.iter().any(|ext| path.ends_with(ext)) && !SKIPPED_PATH_WORDS.iter().any(|word| lowered.contains(word))
}

/// 起始網址若位於子目錄（如 /dept/ais/），回傳該目錄作為範圍提示。
fn path_prefix(url: &str) -> Option<String> {
    let path = Url::parse(url).ok()?.path().to_owned();
    let directory = &path[..=path.rfind('/')?];
    (directory.matches('/').count() >= 2).then(|| directory.to_owned())
}

struct Exploration<'a> {
    base: &'a str,
    terms: &'a [String],
    hints: Vec<&'static str>,
    prefix: Option<String>,
    candidates: HashMap<String, Candidate>,
    /// 維持加入順序，分數相同時結果穩定
    order: Vec<String>,
}

impl Exploration<'_> {
    fn add(&mut self, url: &str, text: &str, bonus: f64) {
        let key = normalize_url(url);
        if key == normalize_url(self.base) || !same_site(url, self.base) || !crawlable(url) {
            return;
        }
        let words = format!("{text} {}", url_words(url));
        let mut score = relevance(self.terms, &words) + bonus;
        let lowered = words.to_lowercase();
        if self.hints.iter().any(|hint| lowered.contains(hint)) {
            score += HINT_WEIGHT;
        }
        // 同一個子目錄（例如系所自己的路徑）下的頁面略微優先
        if let Some(prefix) = &self.prefix
            && Url::parse(url).is_ok_and(|u| u.path().starts_with(prefix.as_str()))
        {
            score += 0.3;
        }

        match self.candidates.get_mut(&key) {
            Some(existing) if score > existing.score => {
                existing.score = score;
                existing.url = url.to_owned();
                if !text.is_empty() {
                    existing.text = text.to_owned();
                }
            }
            Some(_) => {}
            None => {
                self.order.push(key.clone());
                self.candidates.insert(key, Candidate { url: url.to_owned(), text: text.to_owned(), score });
            }
        }
    }

    fn top(&self, tried: &HashSet<String>, count: usize) -> Vec<Candidate> {
        let mut ranked: Vec<&Candidate> = self
            .order
            .iter()
            .filter(|key| !tried.contains(*key))
            .filter_map(|key| self.candidates.get(key))
            .filter(|candidate| candidate.score > 0.0)
            .collect();
        ranked.sort_by(|a, b| b.score.total_cmp(&a.score));
        ranked.into_iter().take(count).cloned().collect()
    }
}

pub struct SiteExplorer {
    fetcher: Arc<PageFetcher>,
    engine: Arc<SearchEngine>,
    settings: Arc<Settings>,
}

impl SiteExplorer {
    pub fn new(fetcher: Arc<PageFetcher>, engine: Arc<SearchEngine>, settings: Arc<Settings>) -> Self {
        Self { fetcher, engine, settings }
    }

    pub async fn explore(&self, url: &str, query: &str, max_pages: usize) -> SiteReport {
        let url = url.trim();
        let url = if url.starts_with("http://") || url.starts_with("https://") { url.to_owned() } else { format!("https://{url}") };
        let terms = query_terms(query);
        if terms.is_empty() {
            return SiteReport { error: Some("請提供要在網站中尋找的內容".into()), ..SiteReport::default() };
        }
        let max_pages = max_pages.clamp(1, HARD_MAX_PAGES);

        let start = match self.fetcher.fetch(&url).await {
            Ok(page) => page,
            Err(error) => {
                return SiteReport { site: url, error: Some(format!("無法開啟網站：{error}")), ..SiteReport::default() };
            }
        };
        let base = start.url.clone();

        let mut exploration = Exploration {
            base: &base,
            terms: &terms,
            hints: section_hints(query),
            prefix: path_prefix(&base),
            candidates: HashMap::new(),
            order: Vec::new(),
        };
        for link in &start.links {
            exploration.add(&link.url, &link.text, 0.0);
        }

        let (sitemap, site_search) = tokio::join!(self.fetcher.sitemap_urls(&base), self.site_search(&base, query));
        for sitemap_url in &sitemap {
            exploration.add(sitemap_url, "", 0.0);
        }
        for entry in &site_search {
            // 搜尋引擎認為相關的頁面額外加分
            exploration.add(&entry.url, &format!("{} {}", entry.title, entry.snippet), 1.5);
        }

        let start_key = normalize_url(&base);
        let mut pages: Vec<Arc<FetchedPage>> = vec![Arc::clone(&start)];
        let mut tried: HashSet<String> = HashSet::from([start_key]);

        let first = exploration.top(&tried, if max_pages > 1 { FIRST_ROUND } else { 0 });
        let fetched = self.fetch_all(first, &mut tried).await;

        // 第二層：從剛讀到的頁面再找更相關的連結（例如「課程資訊 → 課程地圖.pdf」）
        for page in &fetched {
            for link in &page.links {
                exploration.add(&link.url, &link.text, 0.0);
            }
        }
        pages.extend(fetched);

        let remaining = max_pages.saturating_sub(pages.len() - 1);
        if remaining > 0 {
            let best = exploration.candidates.values().map(|c| c.score).fold(0.0, f64::max);
            let threshold = (best * 0.4).max(1.0);
            let second: Vec<Candidate> =
                exploration.top(&tried, remaining).into_iter().filter(|c| c.score >= threshold).collect();
            pages.extend(self.fetch_all(second, &mut tried).await);
        }

        self.report(&base, query, &terms, &start, &pages, &exploration, &tried)
    }

    async fn site_search(&self, base: &str, query: &str) -> Vec<super::engine::SearchResult> {
        if !self.engine.enabled() {
            return Vec::new();
        }
        let host = hostname(base);
        let host = host.strip_prefix("www.").unwrap_or(&host).to_owned();
        let options = SearchOptions { site: host.clone(), max_results: Some(10), ..SearchOptions::default() };
        self.engine.search(&format!("{query} site:{host}"), &options).await.results
    }

    async fn fetch_all(&self, chosen: Vec<Candidate>, tried: &mut HashSet<String>) -> Vec<Arc<FetchedPage>> {
        for candidate in &chosen {
            tried.insert(normalize_url(&candidate.url));
        }
        stream::iter(chosen)
            .map(|candidate| async move {
                if !self.fetcher.allowed_by_robots(&candidate.url).await {
                    tracing::info!(url = %candidate.url, "skipped: disallowed by robots.txt");
                    return None;
                }
                match self.fetcher.fetch(&candidate.url).await {
                    Ok(page) if page.text.as_deref().is_some_and(|text| !text.is_empty()) => Some(page),
                    Ok(_) => None,
                    Err(error) => {
                        tracing::info!(url = %candidate.url, %error, "site explore: fetch failed");
                        None
                    }
                }
            })
            // buffered 維持原本的順序，讓相同輸入得到相同輸出
            .buffered(CONCURRENCY)
            .filter_map(|page| async move { page })
            .collect()
            .await
    }

    #[allow(clippy::too_many_arguments)]
    fn report(
        &self,
        base: &str,
        query: &str,
        terms: &[String],
        start: &FetchedPage,
        pages: &[Arc<FetchedPage>],
        exploration: &Exploration<'_>,
        tried: &HashSet<String>,
    ) -> SiteReport {
        let mut scored: Vec<(f64, &FetchedPage)> = pages
            .iter()
            .map(|page| (page_score(terms, page), page.as_ref()))
            .filter(|(score, _)| *score > 0.0)
            .collect();
        scored.sort_by(|a, b| b.0.total_cmp(&a.0));

        let mut budget = self.settings.fetch_max_chars;
        let per_page = MIN_PASSAGE_CHARS.max(budget / scored.len().max(1));
        let mut pages_read = Vec::new();
        for (_, page) in &scored {
            if budget == 0 {
                break;
            }
            let passages = best_passages(page.text.as_deref().unwrap_or(""), terms, per_page.min(budget), 1);
            if passages.is_empty() {
                continue;
            }
            budget = budget.saturating_sub(passages.chars().count());
            pages_read.push(PageExcerpt { url: page.url.clone(), title: truncate(&page.title, 120), passages });
        }

        let other_relevant_links = exploration
            .top(tried, 10)
            .into_iter()
            .map(|candidate| LinkRef { text: truncate(&candidate.text, 80), url: candidate.url })
            .collect();

        let nothing_found = pages_read.is_empty();
        SiteReport {
            site: hostname(base),
            query: query.to_owned(),
            pages_checked: pages.len(),
            pages_read,
            other_relevant_links,
            site_menu: if nothing_found { menu(&start.links, base, 30) } else { Vec::new() },
            note: nothing_found.then(|| NOTHING_FOUND_NOTE.to_owned()),
            error: None,
        }
    }
}

/// 頁面本身內容的相關度。
///
/// 只是「連結文字」符合的目錄頁要排在真正含有內容的頁面後面，
/// 因此先以扣除連結文字後的內文計分，內文完全不符時才以較低權重採計整頁。
fn page_score(terms: &[String], page: &FetchedPage) -> f64 {
    let text: String = page.text.as_deref().unwrap_or("").chars().take(PAGE_SCORE_CHARS).collect();
    let link_texts: HashSet<&str> = page.links.iter().map(|link| link.text.as_str()).filter(|t| !t.is_empty()).collect();
    let body: Vec<&str> = text.lines().filter(|line| !link_texts.contains(line)).collect();
    let own = relevance(terms, &format!("{} {}", page.title, body.join("\n")));
    if own > 0.0 { own } else { 0.5 * relevance(terms, &text) }
}

fn menu(links: &[Link], base: &str, limit: usize) -> Vec<LinkRef> {
    let mut seen: HashSet<&str> = HashSet::new();
    links
        .iter()
        .filter(|link| !link.text.is_empty() && same_site(&link.url, base) && crawlable(&link.url))
        .filter(|link| seen.insert(link.text.as_str()))
        .take(limit)
        .map(|link| LinkRef { text: truncate(&link.text, 60), url: link.url.clone() })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn helpers() {
        assert!(crawlable("https://x.test/course"));
        assert!(!crawlable("https://x.test/logo.PNG") && !crawlable("https://x.test/login?next=/"));
        assert_eq!(path_prefix("https://x.test/dept/ais/index.html").as_deref(), Some("/dept/ais/"));
        assert_eq!(path_prefix("https://x.test/"), None);
        assert!(url_words("https://x.test/%E8%AA%B2%E7%A8%8B-map?id=1").contains("課程 map"));
        assert!(section_hints("必修科目").contains(&"課程"));
        assert!(section_hints("宿舍").is_empty());
    }
}
