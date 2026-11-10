use rusqlite::params;

use super::Database;
use crate::error::Result;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct TokenUsage {
    pub requests: u64,
    pub input_tokens: u64,
    pub output_tokens: u64,
}

impl TokenUsage {
    pub fn total_tokens(&self) -> u64 {
        self.input_tokens + self.output_tokens
    }

    pub fn add(&mut self, other: TokenUsage) {
        self.requests += other.requests;
        self.input_tokens += other.input_tokens;
        self.output_tokens += other.output_tokens;
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct UsageSummary {
    pub messages: u64,
    pub requests: u64,
    pub input_tokens: u64,
    pub output_tokens: u64,
}

impl UsageSummary {
    pub fn total_tokens(&self) -> u64 {
        self.input_tokens + self.output_tokens
    }
}

/// 依使用者與日期（UTC）累計 AI token 用量。
#[derive(Clone)]
pub struct UsageStore {
    database: Database,
}

impl UsageStore {
    pub fn new(database: Database) -> Self {
        Self { database }
    }

    pub async fn record(&self, user_id: u64, usage: TokenUsage) -> Result<()> {
        self.database
            .call(move |conn| {
                conn.execute(
                    "INSERT INTO token_usage (user_id, day, requests, input_tokens, output_tokens, messages)
                     VALUES (?1, date('now'), ?2, ?3, ?4, 1)
                     ON CONFLICT(user_id, day) DO UPDATE SET
                         requests = requests + excluded.requests,
                         input_tokens = input_tokens + excluded.input_tokens,
                         output_tokens = output_tokens + excluded.output_tokens,
                         messages = messages + 1",
                    params![user_id as i64, usage.requests as i64, usage.input_tokens as i64, usage.output_tokens as i64],
                )
                .map(drop)
            })
            .await
    }

    /// `user_id` 為 None 時統計全體；`days` 為 None 時統計全部期間。
    pub async fn summary(&self, user_id: Option<u64>, days: Option<u32>) -> Result<UsageSummary> {
        self.database
            .call(move |conn| {
                conn.query_row(
                    "SELECT COALESCE(SUM(messages), 0), COALESCE(SUM(requests), 0),
                            COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0)
                     FROM token_usage
                     WHERE (?1 IS NULL OR user_id = ?1)
                     AND (?2 IS NULL OR day > date('now', ?2))",
                    params![user_id.map(|id| id as i64), days.map(|d| format!("-{d} days"))],
                    |row| {
                        Ok(UsageSummary {
                            messages: row.get::<_, i64>(0)? as u64,
                            requests: row.get::<_, i64>(1)? as u64,
                            input_tokens: row.get::<_, i64>(2)? as u64,
                            output_tokens: row.get::<_, i64>(3)? as u64,
                        })
                    },
                )
            })
            .await
    }

    pub async fn top_users(&self, days: u32, limit: usize) -> Result<Vec<(u64, UsageSummary)>> {
        self.database
            .call(move |conn| {
                conn.prepare(
                    "SELECT user_id, SUM(messages), SUM(requests), SUM(input_tokens), SUM(output_tokens)
                     FROM token_usage WHERE day > date('now', ?1)
                     GROUP BY user_id ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC LIMIT ?2",
                )?
                .query_map(params![format!("-{days} days"), limit as i64], |row| {
                    Ok((
                        row.get::<_, i64>(0)? as u64,
                        UsageSummary {
                            messages: row.get::<_, i64>(1)? as u64,
                            requests: row.get::<_, i64>(2)? as u64,
                            input_tokens: row.get::<_, i64>(3)? as u64,
                            output_tokens: row.get::<_, i64>(4)? as u64,
                        },
                    ))
                })?
                .collect()
            })
            .await
    }

    pub async fn delete_user(&self, user_id: u64) -> Result<()> {
        self.database
            .call(move |conn| conn.execute("DELETE FROM token_usage WHERE user_id = ?1", [user_id as i64]).map(drop))
            .await
    }
}
