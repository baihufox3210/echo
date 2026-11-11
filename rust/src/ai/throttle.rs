//! AI API 請求節流：所有模型請求（主代理、研究子代理、備援模型、重試）共用。
//!
//! - 每分鐘請求上限（可選）：送出前排隊，避免撞到供應商的 RPM 額度。
//! - 429 冷卻：任一請求收到 429 後，所有請求暫停一段時間（優先採用 Retry-After），
//!   避免多位使用者與自動重試在額度用盡時持續打 API，讓情況更糟。

use std::collections::VecDeque;
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tokio::sync::Mutex as AsyncMutex;

use super::context::RunContext;

const WINDOW: Duration = Duration::from_secs(60);
const MAX_COOLDOWN: Duration = Duration::from_secs(60);
/// 等待超過此時間時通知使用者「排隊中」，避免短暫等待也跳出提示
const NOTIFY_AFTER: Duration = Duration::from_secs(1);
const BUSY_NOTICE: &str = "◷ AI 忙線中，稍候自動重試…";

/// 解析 Retry-After 標頭（秒數或 HTTP 日期）。
pub fn parse_retry_after(value: Option<&str>) -> Option<Duration> {
    let value = value?.trim();
    if let Ok(seconds) = value.parse::<f64>() {
        return Some(Duration::from_secs_f64(seconds.max(0.0)));
    }
    let when = chrono::DateTime::parse_from_rfc2822(value).ok()?;
    let now = SystemTime::now().duration_since(UNIX_EPOCH).ok()?.as_secs() as i64;
    Some(Duration::from_secs((when.timestamp() - now).max(0) as u64))
}

struct State {
    cooldown_until: Option<Instant>,
    recent: VecDeque<Instant>,
}

pub struct ApiThrottle {
    requests_per_minute: usize,
    cooldown: Duration,
    state: Mutex<State>,
    /// 序列化排程，確保每分鐘上限計算正確，且等待中的請求依序送出
    queue: AsyncMutex<()>,
}

impl ApiThrottle {
    pub fn new(requests_per_minute: u32, cooldown: Duration) -> Self {
        Self {
            requests_per_minute: requests_per_minute as usize,
            cooldown,
            state: Mutex::new(State { cooldown_until: None, recent: VecDeque::new() }),
            queue: AsyncMutex::new(()),
        }
    }

    fn wait_time(&self, now: Instant) -> Duration {
        let mut state = self.state.lock().unwrap_or_else(|p| p.into_inner());
        let mut wait = state.cooldown_until.map_or(Duration::ZERO, |until| until.saturating_duration_since(now));
        if self.requests_per_minute > 0 {
            while state.recent.front().is_some_and(|first| now.duration_since(*first) >= WINDOW) {
                state.recent.pop_front();
            }
            if state.recent.len() >= self.requests_per_minute
                && let Some(first) = state.recent.front()
            {
                wait = wait.max(WINDOW.saturating_sub(now.duration_since(*first)));
            }
        }
        wait
    }

    /// 送出請求前呼叫：必要時等待，並在等待較久時通知使用者。
    pub async fn acquire(&self, context: Option<&RunContext>) {
        // 在排隊前先通知：前面的請求可能正持有佇列並等待中
        let estimated = self.wait_time(Instant::now());
        if estimated >= NOTIFY_AFTER || self.queue.try_lock().is_err() {
            tracing::info!(wait_secs = estimated.as_secs_f64(), "throttling AI request");
            if let Some(context) = context {
                context.status(BUSY_NOTICE);
            }
        }

        let _turn = self.queue.lock().await;
        loop {
            let wait = self.wait_time(Instant::now());
            if wait.is_zero() {
                break;
            }
            tokio::time::sleep(wait).await;
        }
        if self.requests_per_minute > 0 {
            self.state.lock().unwrap_or_else(|p| p.into_inner()).recent.push_back(Instant::now());
        }
    }

    /// 收到 429 時呼叫：所有請求暫停，優先採用 API 指定的時間。
    pub fn rate_limited(&self, retry_after: Option<Duration>) {
        let delay = retry_after.unwrap_or(self.cooldown).min(MAX_COOLDOWN);
        let until = Instant::now() + delay;
        let mut state = self.state.lock().unwrap_or_else(|p| p.into_inner());
        if state.cooldown_until.is_none_or(|current| until > current) {
            state.cooldown_until = Some(until);
            tracing::warn!(
                delay_secs = delay.as_secs_f64(),
                has_retry_after = retry_after.is_some(),
                "AI API rate limited (429); pausing all AI requests"
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn retry_after_parsing() {
        assert_eq!(parse_retry_after(Some("7")), Some(Duration::from_secs(7)));
        assert_eq!(parse_retry_after(None), None);
        assert_eq!(parse_retry_after(Some("garbage")), None);
        assert_eq!(parse_retry_after(Some("Wed, 21 Oct 2015 07:28:00 GMT")), Some(Duration::ZERO));
    }

    #[tokio::test]
    async fn rate_limit_pauses_following_requests() {
        let throttle = ApiThrottle::new(0, Duration::from_millis(300));
        throttle.rate_limited(None);
        let start = Instant::now();
        throttle.acquire(None).await;
        assert!(start.elapsed() >= Duration::from_millis(250));
    }

    #[tokio::test]
    async fn retry_after_overrides_default_and_success_does_not_pause() {
        let throttle = ApiThrottle::new(0, Duration::from_secs(5));
        let start = Instant::now();
        throttle.acquire(None).await;
        assert!(start.elapsed() < Duration::from_millis(50));

        throttle.rate_limited(Some(Duration::from_millis(200)));
        let start = Instant::now();
        throttle.acquire(None).await;
        let elapsed = start.elapsed();
        assert!(elapsed >= Duration::from_millis(150) && elapsed < Duration::from_secs(1));
    }

    #[test]
    fn requests_per_minute_limit() {
        let throttle = ApiThrottle::new(2, Duration::from_secs(1));
        let now = Instant::now();
        {
            let mut state = throttle.state.lock().unwrap();
            state.recent.push_back(now);
            state.recent.push_back(now);
        }
        let wait = throttle.wait_time(now + Duration::from_secs(10));
        assert!(wait > Duration::from_secs(49) && wait <= Duration::from_secs(50));
        assert!(throttle.wait_time(now + Duration::from_secs(61)).is_zero());
    }
}
