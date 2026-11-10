//! 以本機的模擬搜尋引擎與模擬系所網站測試搜尋流程（對應 Python 版 tests/test_web_search.py）。

use std::collections::HashMap;
use std::sync::Arc;

use echo::config::Settings;
use echo::search::engine::SearchOptions;
use echo::search::site::DEFAULT_MAX_PAGES;
use echo::search::{PageFetcher, SearchEngine, SiteExplorer};
use serde_json::{Value, json};
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, Request, ResponseTemplate};

const FORMAL_NAME: &str = "人工智慧與資訊安全學士學位學程";
const NAV: &str = r#"<nav><a href="/">首頁</a><a href="/news">最新消息</a><a href="/course">課程資訊</a><a href="/faculty">師資陣容</a><a href="/secret">內部文件</a></nav>"#;

fn settings(searxng: Option<&str>, overrides: &[(&str, &str)]) -> Arc<Settings> {
    let mut vars: HashMap<String, String> = [
        ("DISCORD_TOKEN", "t"),
        ("BASE_URL", "http://localhost:1/v1"),
        ("API_KEY", "k"),
        ("MODEL", "m"),
        ("FETCH_ALLOW_PRIVATE", "true"),
        ("SEARXNG_URL", searxng.unwrap_or("")),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_owned(), v.to_owned()))
    .collect();
    for (key, value) in overrides {
        vars.insert((*key).to_owned(), (*value).to_owned());
    }
    Arc::new(Settings::from_map(vars).unwrap())
}

fn html(title: &str, body: &str) -> ResponseTemplate {
    ResponseTemplate::new(200).set_body_raw(
        format!("<html><head><title>{title}</title></head><body>{body}</body></html>"),
        "text/html; charset=utf-8",
    )
}

/// 模擬的系所網站：首頁選單 → 課程資訊 → 課程地圖（第二層才有必修科目）。
async fn department_site() -> MockServer {
    let server = MockServer::start().await;
    let uri = server.uri();
    let pages: Vec<(&str, ResponseTemplate)> = vec![
        ("/", html(&format!("{FORMAL_NAME} - 輔仁大學"), &format!("{NAV}<main>歡迎來到本學程</main>"))),
        ("/news", html("最新消息", &format!("{NAV}<main>113 學年度迎新活動</main>"))),
        ("/faculty", html("師資陣容", &format!("{NAV}<main>王教授：密碼學</main>"))),
        (
            "/course",
            html(
                "課程資訊",
                &format!(r#"{NAV}<main>課程規劃說明<a href="/files/map">課程地圖與必修科目表</a><a href="/files/rules">修業規則</a></main>"#),
            ),
        ),
        (
            "/files/map",
            html("課程地圖", "<main><p>大一必修科目：程式設計、計算機概論、資訊安全導論</p><p>大二必修科目：資料結構、機器學習</p><p>畢業學分：128</p></main>"),
        ),
        ("/files/rules", html("修業規則", "<main>英語能力畢業門檻說明</main>")),
        ("/secret", html("內部文件", "<main>必修科目草案（未公開）</main>")),
        ("/admission-info", html("招生說明", "<main>招生名額 50 名</main>")),
        (
            "/robots.txt",
            ResponseTemplate::new(200)
                .set_body_raw(format!("User-agent: *\nDisallow: /secret\nSitemap: {uri}/sitemap.xml\n"), "text/plain"),
        ),
        (
            "/sitemap.xml",
            ResponseTemplate::new(200)
                .set_body_raw(format!("<urlset><url><loc>{uri}/admission-info</loc></url></urlset>"), "application/xml"),
        ),
    ];
    for (route, response) in pages {
        Mock::given(method("GET")).and(path(route)).respond_with(response).mount(&server).await;
    }
    server
}

async fn requested_paths(server: &MockServer) -> Vec<String> {
    server.received_requests().await.unwrap().iter().map(|r| r.url.path().to_owned()).collect()
}

/// 模擬的 SearXNG：以 `respond` 依查詢字串決定結果。
async fn searx(respond: impl Fn(&str) -> Value + Send + Sync + 'static) -> MockServer {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/search"))
        .respond_with(move |request: &Request| {
            let query = request.url.query_pairs().find(|(key, _)| key == "q").map(|(_, value)| value.into_owned());
            ResponseTemplate::new(200).set_body_json(json!({ "results": respond(&query.unwrap_or_default()) }))
        })
        .mount(&server)
        .await;
    server
}

fn explorer(settings: &Arc<Settings>) -> SiteExplorer {
    SiteExplorer::new(
        Arc::new(PageFetcher::new(Arc::clone(settings))),
        Arc::new(SearchEngine::new(Arc::clone(settings))),
        Arc::clone(settings),
    )
}

// ---------- 第一階段：模糊探索 ----------

#[tokio::test]
async fn discover_resolves_abbreviation_to_formal_name() {
    let server = searx(|_| {
        json!([
            {"title": "輔大智慧資安學程心得 - Dcard", "url": "https://www.dcard.tw/f/fju/p/1", "content": "想問智慧資安"},
            {"title": format!("{FORMAL_NAME} - 輔仁大學"), "url": "https://ais.fju.edu.tw/", "content": "本學程培育 AI 與資安人才"},
            {"title": format!("學程簡介 | {FORMAL_NAME}"), "url": "https://ais.fju.edu.tw/about", "content": "簡介"},
        ])
    })
    .await;
    let engine = SearchEngine::new(settings(Some(&server.uri()), &[]));

    let payload = engine.discover("輔大智慧資安").await;

    let queries: Vec<String> = server
        .received_requests()
        .await
        .unwrap()
        .iter()
        .filter_map(|r| r.url.query_pairs().find(|(k, _)| k == "q").map(|(_, v)| v.into_owned()))
        .collect();
    for expected in ["輔大智慧資安", "輔大智慧資安 官網", "輔大智慧資安 是什麼"] {
        assert!(queries.iter().any(|q| q == expected), "缺少查詢 {expected}");
    }

    assert_eq!(payload.candidate_names[0].name, FORMAL_NAME);
    let site = &payload.sites[0];
    assert_eq!((site.site.as_str(), site.official, site.pages), ("ais.fju.edu.tw", true, 2));
    assert_eq!(site.example, "https://ais.fju.edu.tw/");
    // 社群網站不列為可探索的官方網站，且官方結果排在前面
    assert!(payload.sites.iter().all(|s| !s.site.contains("dcard")));
    assert_eq!(payload.results[0].official, Some(true));
    assert!(payload.next_steps.is_some());
}

// ---------- 第二階段：精確搜尋 ----------

#[tokio::test]
async fn search_dedups_caches_and_filters_site() {
    let server = searx(|_| {
        json!([
            {"title": "A", "url": "https://a.com/x?utm_source=1", "content": "first", "publishedDate": "2026-10-01T00:00:00"},
            {"title": "A dup", "url": "https://a.com/x", "content": "dup"},
            {"title": "B", "url": "https://b.com", "content": "second"},
        ])
    })
    .await;
    let engine = SearchEngine::new(settings(Some(&server.uri()), &[]));
    let weekly = SearchOptions { time_range: "week".into(), ..SearchOptions::default() };

    let first = engine.search("news", &weekly).await;
    let second = engine.search("news", &weekly).await;
    assert_eq!(first, second);
    assert_eq!(server.received_requests().await.unwrap().len(), 1);
    assert!(server.received_requests().await.unwrap()[0].url.query().unwrap().contains("time_range=week"));
    assert_eq!(first.results.iter().map(|r| r.title.as_str()).collect::<Vec<_>>(), ["A", "B"]);
    assert_eq!(first.results[0].published.as_deref(), Some("2026-10-01"));

    let only_a = engine.search("news site:a.com", &SearchOptions { site: "a.com".into(), ..SearchOptions::default() }).await;
    assert_eq!(only_a.results.iter().map(|r| r.title.as_str()).collect::<Vec<_>>(), ["A"]);
}

#[tokio::test]
async fn search_many_merges_with_rrf() {
    let server = searx(|query| {
        json!([
            {"title": "shared", "url": "https://shared.com", "content": ""},
            {"title": query, "url": format!("https://{query}.com"), "content": ""},
        ])
    })
    .await;
    let engine = SearchEngine::new(settings(Some(&server.uri()), &[]));

    let queries = ["alpha".to_owned(), "beta".to_owned(), "alpha".to_owned()];
    let merged = engine.search_many(&queries, &SearchOptions::default()).await;

    assert_eq!(merged.queries, ["alpha", "beta"]);
    assert_eq!(merged.results[0].url, "https://shared.com");
    assert_eq!(merged.results[0].matched_queries, ["alpha", "beta"]);
    assert_eq!(merged.results.len(), 3);
}

#[tokio::test]
async fn search_reports_errors_without_engine() {
    let disabled = SearchEngine::new(settings(None, &[]));
    assert!(!disabled.enabled());
    assert!(disabled.search("x", &SearchOptions::default()).await.error.is_some());

    let unreachable = SearchEngine::new(settings(Some("http://127.0.0.1:9"), &[("SEARCH_TIMEOUT", "2")]));
    let payload = unreachable.search("x", &SearchOptions::default()).await;
    assert!(payload.error.unwrap().starts_with("搜尋失敗"));
}

// ---------- 第三階段：站內查找 ----------

#[tokio::test]
async fn explore_site_follows_menu_to_second_level_page() {
    let site = department_site().await;
    let report = explorer(&settings(None, &[])).explore(&site.uri(), "必修科目", DEFAULT_MAX_PAGES).await;

    // 首頁選單沒有「必修科目」，要先進「課程資訊」再進「課程地圖」
    let map = format!("{}/files/map", site.uri());
    assert_eq!(report.pages_read[0].url, map);
    assert!(report.pages_read[0].passages.contains("資訊安全導論"));

    // 不相關的頁面不會被讀取；robots.txt 禁止的頁面即使相關也不讀
    let requested = requested_paths(&site).await;
    assert!(!requested.iter().any(|p| p == "/faculty" || p == "/news"));
    assert!(!requested.iter().any(|p| p == "/secret"));
    assert!(report.error.is_none() && report.note.is_none());
}

#[tokio::test]
async fn explore_site_uses_sitemap_and_site_search() {
    let site = department_site().await;
    let rules = format!("{}/files/rules", site.uri());
    let rules_for_search = rules.clone();
    let search = searx(move |query| {
        assert!(query.contains("site:127.0.0.1"), "站內搜尋應帶 site: 條件：{query}");
        json!([{"title": "修業規則", "url": rules_for_search, "content": "英語能力畢業門檻"}])
    })
    .await;
    let explorer = explorer(&settings(Some(&search.uri()), &[]));

    // 只出現在 sitemap 的頁面
    let report = explorer.explore(&site.uri(), "招生名額", DEFAULT_MAX_PAGES).await;
    assert!(report.pages_read.iter().any(|p| p.url.ends_with("/admission-info") && p.passages.contains("50 名")));

    // 搜尋引擎的站內搜尋結果優先
    let report = explorer.explore(&site.uri(), "英語畢業門檻", DEFAULT_MAX_PAGES).await;
    assert_eq!(report.pages_read[0].url, rules);
}

#[tokio::test]
async fn explore_site_reports_menu_when_nothing_matches() {
    let site = department_site().await;
    let report = explorer(&settings(None, &[])).explore(&site.uri(), "宿舍申請", DEFAULT_MAX_PAGES).await;

    assert!(report.pages_read.is_empty());
    assert!(report.site_menu.iter().any(|link| link.text == "課程資訊" && link.url.ends_with("/course")));
    assert!(report.note.is_some());

    let json = serde_json::to_value(&report).unwrap();
    assert!(json.get("site_menu").is_some() && json.get("error").is_none());
}

// ---------- 抓取 ----------

#[tokio::test]
async fn fetch_blocks_private_addresses_by_default() {
    let fetcher = PageFetcher::new(settings(None, &[("FETCH_ALLOW_PRIVATE", "false")]));
    assert!(fetcher.fetch("http://127.0.0.1:8080/").await.unwrap_err().0.contains("內部網路"));
    assert!(fetcher.fetch("http://169.254.169.254/latest").await.unwrap_err().0.contains("內部網路"));
    assert!(fetcher.fetch("file:///etc/passwd").await.unwrap_err().0.contains("http/https"));
}

#[tokio::test]
async fn fetch_follows_redirects_and_handles_non_text() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/old"))
        .respond_with(ResponseTemplate::new(301).insert_header("location", "/new"))
        .mount(&server)
        .await;
    Mock::given(method("GET")).and(path("/new")).respond_with(html("新頁面", "<main>正文內容</main>")).mount(&server).await;
    Mock::given(method("GET"))
        .and(path("/logo.png"))
        .respond_with(ResponseTemplate::new(200).set_body_raw(vec![0x89, b'P', b'N', b'G'], "image/png"))
        .mount(&server)
        .await;
    let fetcher = PageFetcher::new(settings(None, &[]));

    let page = fetcher.fetch(&format!("{}/old", server.uri())).await.unwrap();
    assert_eq!(page.url, format!("{}/new", server.uri()));
    assert_eq!((page.title.as_str(), page.text.as_deref()), ("新頁面", Some("正文內容")));

    let image = fetcher.fetch(&format!("{}/logo.png", server.uri())).await.unwrap();
    assert!(image.text.is_none());
    assert_eq!(fetcher.fetch(&format!("{}/missing", server.uri())).await.unwrap_err().0, "HTTP 404");
}
