//! 併發控制：依 key 的互斥鎖與滑動視窗限流。

use std::collections::{HashMap, VecDeque};
use std::hash::Hash;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tokio::sync::{Mutex as AsyncMutex, OwnedMutexGuard};

/// 依 key 取得獨立的鎖：同一 key 依序執行，不同 key 可並行。
pub struct KeyedLock<K> {
    locks: Mutex<HashMap<K, Arc<AsyncMutex<()>>>>,
}

impl<K> Default for KeyedLock<K> {
    fn default() -> Self {
        Self { locks: Mutex::new(HashMap::new()) }
    }
}

/// 持有期間獨占該 key；釋放時若已無人等待，會清除該 key 的鎖。
pub struct KeyedGuard<'a, K: Eq + Hash + Clone> {
    owner: &'a KeyedLock<K>,
    key: K,
    lock: Arc<AsyncMutex<()>>,
    guard: Option<OwnedMutexGuard<()>>,
}

impl<K: Eq + Hash + Clone> KeyedLock<K> {
    pub fn new() -> Self {
        Self::default()
    }

    pub async fn lock(&self, key: K) -> KeyedGuard<'_, K> {
        let lock = {
            let mut locks = self.locks.lock().unwrap_or_else(|p| p.into_inner());
            Arc::clone(locks.entry(key.clone()).or_default())
        };
        let guard = Arc::clone(&lock).lock_owned().await;
        KeyedGuard { owner: self, key, lock, guard: Some(guard) }
    }

    pub fn is_busy(&self, key: &K) -> bool {
        self.locks.lock().unwrap_or_else(|p| p.into_inner()).contains_key(key)
    }
}

impl<K: Eq + Hash + Clone> Drop for KeyedGuard<'_, K> {
    fn drop(&mut self) {
        self.guard.take();
        let mut locks = self.owner.locks.lock().unwrap_or_else(|p| p.into_inner());
        // 參考計數為 2 代表只剩 map 與這個 guard 持有，沒有其他等待者
        if Arc::strong_count(&self.lock) <= 2 {
            locks.remove(&self.key);
        }
    }
}

/// 滑動視窗限流器。
pub struct RateLimiter<K> {
    limit: usize,
    window: Duration,
    state: Mutex<LimiterState<K>>,
}

struct LimiterState<K> {
    hits: HashMap<K, VecDeque<Instant>>,
    last_sweep: Instant,
}

impl<K: Eq + Hash> RateLimiter<K> {
    pub fn new(limit: usize, window: Duration) -> Self {
        Self {
            limit,
            window,
            state: Mutex::new(LimiterState { hits: HashMap::new(), last_sweep: Instant::now() }),
        }
    }

    /// 記錄一次請求；允許時回傳 None，否則回傳需等待的時間。
    pub fn hit(&self, key: K) -> Option<Duration> {
        self.hit_at(key, Instant::now())
    }

    fn hit_at(&self, key: K, now: Instant) -> Option<Duration> {
        let mut state = self.state.lock().unwrap_or_else(|p| p.into_inner());
        let window = self.window;

        if now.duration_since(state.last_sweep) >= window {
            state.last_sweep = now;
            state.hits.retain(|_, hits| hits.back().is_some_and(|last| now.duration_since(*last) < window));
        }

        let hits = state.hits.entry(key).or_default();
        while hits.front().is_some_and(|first| now.duration_since(*first) >= window) {
            hits.pop_front();
        }
        if hits.len() >= self.limit {
            return hits.front().map(|first| window - now.duration_since(*first));
        }
        hits.push_back(now);
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rate_limiter_blocks_after_limit_and_recovers() {
        let limiter = RateLimiter::new(2, Duration::from_secs(60));
        let start = Instant::now();
        assert!(limiter.hit_at(1, start).is_none());
        assert!(limiter.hit_at(1, start).is_none());
        assert!(limiter.hit_at(1, start).is_some());
        assert!(limiter.hit_at(2, start).is_none());
        assert!(limiter.hit_at(1, start + Duration::from_secs(61)).is_none());
    }

    #[tokio::test]
    async fn keyed_lock_serializes_same_key_and_parallelizes_others() {
        let lock = Arc::new(KeyedLock::new());
        let order = Arc::new(Mutex::new(Vec::new()));

        let work = |key: u64, name: &'static str| {
            let (lock, order) = (Arc::clone(&lock), Arc::clone(&order));
            async move {
                let _guard = lock.lock(key).await;
                order.lock().unwrap().push(format!("{name}-start"));
                tokio::time::sleep(Duration::from_millis(50)).await;
                order.lock().unwrap().push(format!("{name}-end"));
            }
        };
        tokio::join!(work(1, "a"), work(1, "b"), work(2, "c"));

        let order = order.lock().unwrap();
        let position = |entry: &str| order.iter().position(|e| e == entry).unwrap();
        assert!(position("a-end") < position("b-start"));
        assert!(position("c-start") < position("a-end"));
        assert!(!lock.is_busy(&1) && !lock.is_busy(&2));
    }
}
