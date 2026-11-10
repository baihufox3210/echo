//! 網頁抓取：SSRF 防護、逐跳檢查重新導向、大小限制、文件轉文字與連結擷取。

use std::net::IpAddr;
use std::sync::{Arc, LazyLock};
use std::time::Duration;

use regex::Regex;
use url::Url;

use super::common::{TtlCache, USER_AGENT, is_public_address};
use crate::config::Settings;
use crate::documents::{DocumentKind, Link, extract_text, kind_of, parse_html};

const MAX_REDIRECTS: usize = 5;
const SITEMAP_MAX_URLS: usize = 2000;
const SITEMAP_MAX_FILES: usize = 4;
const ROBOTS_TTL: Duration = Duration::from_secs(3600);

static LOC_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)<loc>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]+)").unwrap());

/// 無法取得頁面；訊息可直接顯示給模型。
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{0}")]
pub struct FetchError(pub String);

impl FetchError {
    fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct FetchedPage {
    /// 重新導向後的最終網址
    pub url: String,
    pub content_type: String,
    pub title: String,
    /// None 代表資源不是可讀取的文字或文件（例如圖片）
    pub text: Option<String>,
    pub links: Vec<Link>,
}

struct Download {
    url: String,
    content_type: String,
    body: Vec<u8>,
    charset: Option<String>,
}

/// robots.txt 中適用於所有爬蟲（`User-agent: *`）或本 Bot 的規則。
#[derive(Debug, Clone, Default)]
pub struct Robots {
    /// (是否允許, 路徑前綴)
    rules: Vec<(bool, String)>,
    pub sitemaps: Vec<String>,
}

impl Robots {
    pub fn parse(text: &str) -> Self {
        let mut robots = Self::default();
        let mut applies = false;
        let mut in_agent_block = false;

        for line in text.lines() {
            let line = line.split('#').next().unwrap_or("").trim();
            let Some((field, value)) = line.split_once(':') else { continue };
            let (field, value) = (field.trim().to_lowercase(), value.trim());
            match field.as_str() {
                "user-agent" => {
                    // 連續的 User-agent 行屬於同一組規則
                    if !in_agent_block {
                        applies = false;
                    }
                    in_agent_block = true;
                    let agent = value.to_lowercase();
                    if agent == "*" || agent.contains("echobot") {
                        applies = true;
                    }
                }
                "allow" | "disallow" => {
                    in_agent_block = false;
                    if applies && !value.is_empty() {
                        robots.rules.push((field == "allow", value.to_owned()));
                    }
                }
                "sitemap" => robots.sitemaps.push(value.to_owned()),
                _ => in_agent_block = false,
            }
        }
        robots
    }

    /// 以最長符合的前綴決定；長度相同時 Allow 優先。
    pub fn allows(&self, path: &str) -> bool {
        self.rules
            .iter()
            .filter(|(_, prefix)| path.starts_with(prefix.trim_end_matches('*').trim_end_matches('$')))
            .max_by_key(|(allow, prefix)| (prefix.len(), *allow))
            .is_none_or(|(allow, _)| *allow)
    }
}

pub struct PageFetcher {
    settings: Arc<Settings>,
    client: reqwest::Client,
    cache: TtlCache<String, Arc<FetchedPage>>,
    robots: TtlCache<String, Arc<Robots>>,
}

impl PageFetcher {
    pub fn new(settings: Arc<Settings>) -> Self {
        let mut headers = reqwest::header::HeaderMap::new();
        headers.insert(
            reqwest::header::ACCEPT,
            reqwest::header::HeaderValue::from_static("text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.5"),
        );
        if let Ok(language) = format!("{},en;q=0.7", settings.search_language).parse() {
            headers.insert(reqwest::header::ACCEPT_LANGUAGE, language);
        }

        // 自行處理重新導向，以便逐跳檢查目標位址
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs_f64(settings.search_timeout))
            .connect_timeout(Duration::from_secs(10))
            .redirect(reqwest::redirect::Policy::none())
            .user_agent(USER_AGENT)
            .default_headers(headers)
            .build()
            .expect("建立抓取用的 HTTP 客戶端失敗");

        Self {
            cache: TtlCache::new(Duration::from_secs_f64(settings.search_cache_ttl)),
            robots: TtlCache::with_capacity(ROBOTS_TTL, 128),
            client,
            settings,
        }
    }

    /// 下載並轉為純文字（HTML、純文字、PDF、DOCX）。
    pub async fn fetch(&self, url: &str) -> Result<Arc<FetchedPage>, FetchError> {
        let url = url.trim();
        if let Some(cached) = self.cache.get(&url.to_owned()) {
            return Ok(cached);
        }

        let download = self.download(url).await?;
        let path = Url::parse(&download.url).map(|u| u.path().to_owned()).unwrap_or_default();
        let unreadable = |download: &Download| FetchedPage {
            url: download.url.clone(),
            content_type: download.content_type.clone(),
            ..FetchedPage::default()
        };

        let kind = match kind_of(&path, Some(&download.content_type)) {
            Ok(DocumentKind::Image) | Err(_) => return Ok(Arc::new(unreadable(&download))),
            Ok(kind) => kind,
        };

        // 解析可能耗時（大型 HTML、PDF），放到阻塞執行緒
        let page = tokio::task::spawn_blocking(move || -> Result<FetchedPage, FetchError> {
            let charset = download.charset.as_deref();
            let (title, text, links) = if kind == DocumentKind::Html {
                let parsed = parse_html(&download.body, charset, Some(&download.url));
                (parsed.title, parsed.text, parsed.links)
            } else {
                let (title, text) = extract_text(&download.body, kind, charset).map_err(|e| FetchError(e.0))?;
                (title, text, Vec::new())
            };
            Ok(FetchedPage { url: download.url, content_type: download.content_type, title, text: Some(text), links })
        })
        .await
        .map_err(|_| FetchError::new("解析失敗"))??;

        tracing::info!(url = %page.url, chars = page.text.as_deref().map_or(0, str::len), links = page.links.len(), "web fetch");
        let page = Arc::new(page);
        self.cache.set(url.to_owned(), Arc::clone(&page));
        Ok(page)
    }

    async fn download(&self, url: &str) -> Result<Download, FetchError> {
        let mut current = Url::parse(url).map_err(|_| FetchError::new("僅支援 http/https 網址"))?;

        for _ in 0..=MAX_REDIRECTS {
            self.ensure_allowed(&current).await?;
            let mut response = self.client.get(current.clone()).send().await.map_err(|error| {
                FetchError::new(if error.is_timeout() { "連線逾時" } else { "連線失敗" })
            })?;

            let status = response.status();
            if status.is_redirection()
                && let Some(location) = response.headers().get(reqwest::header::LOCATION).and_then(|v| v.to_str().ok())
            {
                current = current.join(location).map_err(|_| FetchError::new("重新導向的網址無效"))?;
                continue;
            }
            if !status.is_success() {
                return Err(FetchError(format!("HTTP {}", status.as_u16())));
            }

            let content_type = response
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .unwrap_or("")
                .to_lowercase();
            let charset = content_type
                .split(';')
                .skip(1)
                .find_map(|part| part.trim().strip_prefix("charset="))
                .map(|charset| charset.trim_matches('"').to_owned());

            let limit = self.settings.fetch_max_bytes;
            let mut body: Vec<u8> = Vec::new();
            while body.len() < limit {
                match response.chunk().await {
                    Ok(Some(chunk)) => body.extend_from_slice(&chunk),
                    Ok(None) => break,
                    Err(_) if !body.is_empty() => break,
                    Err(_) => return Err(FetchError::new("下載中斷")),
                }
            }
            body.truncate(limit);
            return Ok(Download { url: current.to_string(), content_type, body, charset });
        }
        Err(FetchError::new("重新導向次數過多"))
    }

    /// 阻擋非 HTTP(S) 及指向內網、本機的位址，避免 SSRF。
    async fn ensure_allowed(&self, url: &Url) -> Result<(), FetchError> {
        if !matches!(url.scheme(), "http" | "https") {
            return Err(FetchError::new("僅支援 http/https 網址"));
        }
        let Some(host) = url.host_str() else {
            return Err(FetchError::new("僅支援 http/https 網址"));
        };
        if self.settings.fetch_allow_private {
            return Ok(());
        }

        let literal = host.trim_start_matches('[').trim_end_matches(']');
        let addresses: Vec<IpAddr> = match literal.parse::<IpAddr>() {
            Ok(address) => vec![address],
            Err(_) => tokio::net::lookup_host((host, url.port_or_known_default().unwrap_or(443)))
                .await
                .map_err(|_| FetchError::new("無法解析網域"))?
                .map(|socket| socket.ip())
                .collect(),
        };
        if addresses.is_empty() || !addresses.iter().all(|address| is_public_address(*address)) {
            return Err(FetchError::new("不允許存取內部網路位址"));
        }
        Ok(())
    }

    /// 抓取 robots.txt、sitemap 等小型文字檔；任何失敗都回傳 None。
    async fn get_text(&self, url: &str) -> Option<String> {
        let download = self.download(url).await.ok()?;
        Some(String::from_utf8_lossy(&download.body).into_owned())
    }

    async fn robots_for(&self, url: &Url) -> Arc<Robots> {
        let origin = url.origin().ascii_serialization();
        if let Some(cached) = self.robots.get(&origin) {
            return cached;
        }
        let robots = match self.get_text(&format!("{origin}/robots.txt")).await {
            // 有些網站對不存在的路徑回傳 HTML 首頁，不能當成 robots.txt
            Some(text) if !text.chars().take(500).collect::<String>().to_lowercase().contains("<html") => Robots::parse(&text),
            _ => Robots::default(),
        };
        let robots = Arc::new(robots);
        self.robots.set(origin, Arc::clone(&robots));
        robots
    }

    /// 自動探索網站時遵守 robots.txt；讀不到時視為允許。
    pub async fn allowed_by_robots(&self, url: &str) -> bool {
        let Ok(parsed) = Url::parse(url) else { return false };
        self.robots_for(&parsed).await.allows(parsed.path())
    }

    /// 讀取網站的 sitemap（含一層 sitemap index），回傳其中的網址。
    pub async fn sitemap_urls(&self, url: &str) -> Vec<String> {
        let Ok(parsed) = Url::parse(url) else { return Vec::new() };
        let origin = parsed.origin().ascii_serialization();

        let mut pending: Vec<String> = self.robots_for(&parsed).await.sitemaps.clone();
        let default = format!("{origin}/sitemap.xml");
        if !pending.contains(&default) {
            pending.push(default);
        }
        pending.truncate(SITEMAP_MAX_FILES);

        let mut urls: Vec<String> = Vec::new();
        let mut fetched = 0;
        while !pending.is_empty() && fetched < SITEMAP_MAX_FILES && urls.len() < SITEMAP_MAX_URLS {
            let sitemap = pending.remove(0);
            fetched += 1;
            let Some(text) = self.get_text(&sitemap).await else { continue };
            let locations: Vec<String> = LOC_PATTERN.captures_iter(&text).map(|c| c[1].to_owned()).collect();
            let is_index = text.chars().take(2000).collect::<String>().to_lowercase().contains("<sitemapindex");
            for location in locations {
                let target = if is_index { &mut pending } else { &mut urls };
                if !target.contains(&location) {
                    target.push(location);
                }
            }
        }
        urls.truncate(SITEMAP_MAX_URLS);
        urls
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn robots_rules() {
        let robots = Robots::parse(
            "User-agent: googlebot\nDisallow: /\n\nUser-agent: *\nDisallow: /secret\nAllow: /secret/public\n\
             Sitemap: https://x.test/sitemap.xml\n",
        );
        assert!(robots.allows("/course"));
        assert!(!robots.allows("/secret/plan"));
        assert!(robots.allows("/secret/public/page"));
        assert_eq!(robots.sitemaps, ["https://x.test/sitemap.xml"]);
        assert!(Robots::default().allows("/anything"));
    }
}
