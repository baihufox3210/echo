//! 搜尋各模組共用的小工具：快取、網址正規化、位址檢查。

use std::collections::HashMap;
use std::hash::Hash;
use std::net::IpAddr;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use url::Url;

pub const USER_AGENT: &str = "Mozilla/5.0 (compatible; EchoBot/1.0; +https://github.com/baihufox3210/echo)";
const CACHE_MAX_ENTRIES: usize = 256;

/// 有存活時間與容量上限的簡易快取。
pub struct TtlCache<K, V> {
    ttl: Duration,
    max_entries: usize,
    data: Mutex<HashMap<K, (Instant, V)>>,
}

impl<K: Eq + Hash + Clone, V: Clone> TtlCache<K, V> {
    pub fn new(ttl: Duration) -> Self {
        Self::with_capacity(ttl, CACHE_MAX_ENTRIES)
    }

    pub fn with_capacity(ttl: Duration, max_entries: usize) -> Self {
        Self { ttl, max_entries, data: Mutex::new(HashMap::new()) }
    }

    pub fn get(&self, key: &K) -> Option<V> {
        let mut data = self.data.lock().unwrap_or_else(|p| p.into_inner());
        match data.get(key) {
            Some((stored, value)) if stored.elapsed() <= self.ttl => Some(value.clone()),
            Some(_) => {
                data.remove(key);
                None
            }
            None => None,
        }
    }

    pub fn set(&self, key: K, value: V) {
        if self.ttl.is_zero() {
            return;
        }
        let mut data = self.data.lock().unwrap_or_else(|p| p.into_inner());
        data.insert(key, (Instant::now(), value));
        while data.len() > self.max_entries {
            let oldest = data.iter().min_by_key(|(_, (stored, _))| *stored).map(|(k, _)| k.clone());
            match oldest {
                Some(key) => data.remove(&key),
                None => break,
            };
        }
    }
}

/// 去除 fragment、追蹤參數與結尾斜線，用於結果去重。
pub fn normalize_url(url: &str) -> String {
    let Ok(parsed) = Url::parse(url) else {
        return url.to_owned();
    };
    let query: Vec<&str> = parsed
        .query()
        .unwrap_or("")
        .split('&')
        .filter(|part| {
            let lower = part.to_lowercase();
            !part.is_empty() && !lower.starts_with("utm_") && !lower.starts_with("fbclid=") && !lower.starts_with("gclid=")
        })
        .collect();

    let mut normalized = format!("{}://{}", parsed.scheme(), parsed.host_str().unwrap_or("").to_lowercase());
    if let Some(port) = parsed.port() {
        normalized.push_str(&format!(":{port}"));
    }
    normalized.push_str(parsed.path().trim_end_matches('/'));
    if !query.is_empty() {
        normalized.push('?');
        normalized.push_str(&query.join("&"));
    }
    normalized
}

/// 是否為可公開連線的位址；內網、本機、連結本地等位址一律視為不可。
pub fn is_public_address(address: IpAddr) -> bool {
    match address {
        IpAddr::V4(ip) => {
            let octets = ip.octets();
            !(ip.is_private()
                || ip.is_loopback()
                || ip.is_link_local()
                || ip.is_broadcast()
                || ip.is_documentation()
                || ip.is_unspecified()
                || ip.is_multicast()
                || octets[0] == 0
                || (octets[0] == 100 && (64..128).contains(&octets[1])) // 100.64.0.0/10 共享位址
                || (octets[0] == 192 && octets[1] == 0 && octets[2] == 0)
                || (octets[0] == 198 && (18..20).contains(&octets[1]))
                || octets[0] >= 240)
        }
        IpAddr::V6(ip) => {
            if let Some(mapped) = ip.to_ipv4_mapped() {
                return is_public_address(IpAddr::V4(mapped));
            }
            let first = ip.segments()[0];
            !(ip.is_loopback()
                || ip.is_unspecified()
                || ip.is_multicast()
                || (first & 0xfe00) == 0xfc00 // fc00::/7 唯一本地
                || (first & 0xffc0) == 0xfe80 // fe80::/10 連結本地
                || (first == 0x2001 && ip.segments()[1] == 0x0db8))
        }
    }
}

/// 去掉協定、路徑與開頭的 www.，只留網域。
pub fn clean_domain(domain: &str) -> String {
    let domain = domain.trim().to_lowercase();
    let without_scheme = domain.split_once("://").map_or(domain.as_str(), |(_, rest)| rest);
    let host = without_scheme.split('/').next().unwrap_or("");
    host.strip_prefix("www.").unwrap_or(host).to_owned()
}

pub fn hostname(url: &str) -> String {
    Url::parse(url).ok().and_then(|u| u.host_str().map(str::to_lowercase)).unwrap_or_default()
}

fn bare_host(url: &str) -> String {
    let host = hostname(url);
    host.strip_prefix("www.").map(str::to_owned).unwrap_or(host)
}

/// 網址的主機是否為 `domain` 或其子網域。
pub fn host_matches(url: &str, domain: &str) -> bool {
    let host = bare_host(url);
    host == domain || host.ends_with(&format!(".{domain}"))
}

pub fn same_site(url: &str, other: &str) -> bool {
    let host = bare_host(url);
    !host.is_empty() && host == bare_host(other)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_url_strips_tracking() {
        assert_eq!(normalize_url("https://Example.com/a/?utm_source=x&id=1#top"), "https://example.com/a?id=1");
        assert_eq!(normalize_url("https://a.com/x"), normalize_url("https://a.com/x/?utm_source=1"));
    }

    #[test]
    fn public_address_check() {
        for (address, expected) in [
            ("8.8.8.8", true),
            ("127.0.0.1", false),
            ("10.0.0.5", false),
            ("169.254.169.254", false),
            ("100.87.206.83", false),
            ("::1", false),
            ("::ffff:10.0.0.1", false),
            ("2606:4700::1111", true),
        ] {
            assert_eq!(is_public_address(address.parse().unwrap()), expected, "{address}");
        }
    }

    #[test]
    fn domain_helpers() {
        assert_eq!(clean_domain("https://www.Python.org/downloads"), "python.org");
        assert!(host_matches("https://zh.wikipedia.org/wiki/X", "wikipedia.org"));
        assert!(!host_matches("https://notwikipedia.org/", "wikipedia.org"));
        assert!(same_site("https://www.a.edu.tw/x", "https://a.edu.tw/y"));
    }

    #[test]
    fn cache_expires_and_evicts() {
        let cache = TtlCache::with_capacity(Duration::from_secs(60), 2);
        cache.set("a", 1);
        cache.set("b", 2);
        cache.set("c", 3);
        assert_eq!(cache.get(&"c"), Some(3));
        assert_eq!([cache.get(&"a"), cache.get(&"b")].iter().flatten().count(), 1);

        let disabled = TtlCache::new(Duration::ZERO);
        disabled.set("a", 1);
        assert_eq!(disabled.get(&"a"), None);
    }
}
