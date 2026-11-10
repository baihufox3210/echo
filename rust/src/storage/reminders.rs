use chrono::{DateTime, SecondsFormat, Utc};
use rusqlite::{Row, params};

use super::Database;
use crate::error::Result;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Reminder {
    pub id: i64,
    pub user_id: u64,
    pub channel_id: u64,
    pub due_at: DateTime<Utc>,
    pub content: String,
}

const COLUMNS: &str = "id, user_id, channel_id, due_at, content";

/// 與 Python 版相同的格式：UTC、秒精度、`+00:00` 結尾，可依字串順序比較。
fn format_time(time: DateTime<Utc>) -> String {
    time.to_rfc3339_opts(SecondsFormat::Secs, false)
}

fn from_row(row: &Row<'_>) -> rusqlite::Result<Reminder> {
    let due: String = row.get(3)?;
    let due_at = DateTime::parse_from_rfc3339(&due)
        .map(|time| time.with_timezone(&Utc))
        .map_err(|e| rusqlite::Error::FromSqlConversionFailure(3, rusqlite::types::Type::Text, Box::new(e)))?;
    Ok(Reminder {
        id: row.get(0)?,
        user_id: row.get::<_, i64>(1)? as u64,
        channel_id: row.get::<_, i64>(2)? as u64,
        due_at,
        content: row.get(4)?,
    })
}

/// 提醒排程；時間一律以 UTC 保存。
#[derive(Clone)]
pub struct ReminderStore {
    database: Database,
}

impl ReminderStore {
    pub fn new(database: Database) -> Self {
        Self { database }
    }

    pub async fn add(&self, user_id: u64, channel_id: u64, due_at: DateTime<Utc>, content: String) -> Result<i64> {
        self.database
            .call(move |conn| {
                conn.execute(
                    "INSERT INTO reminders (user_id, channel_id, due_at, content) VALUES (?1, ?2, ?3, ?4)",
                    params![user_id as i64, channel_id as i64, format_time(due_at), content],
                )?;
                Ok(conn.last_insert_rowid())
            })
            .await
    }

    pub async fn list_user(&self, user_id: u64) -> Result<Vec<Reminder>> {
        self.database
            .call(move |conn| {
                conn.prepare(&format!("SELECT {COLUMNS} FROM reminders WHERE user_id = ?1 ORDER BY due_at"))?
                    .query_map([user_id as i64], from_row)?
                    .collect()
            })
            .await
    }

    pub async fn count_user(&self, user_id: u64) -> Result<usize> {
        self.database
            .call(move |conn| {
                conn.query_row("SELECT COUNT(*) FROM reminders WHERE user_id = ?1", [user_id as i64], |row| {
                    row.get::<_, i64>(0).map(|n| n as usize)
                })
            })
            .await
    }

    pub async fn cancel(&self, user_id: u64, reminder_id: i64) -> Result<bool> {
        self.database
            .call(move |conn| {
                conn.execute("DELETE FROM reminders WHERE id = ?1 AND user_id = ?2", params![reminder_id, user_id as i64])
                    .map(|rows| rows > 0)
            })
            .await
    }

    /// 取出並刪除已到期的提醒。
    pub async fn pop_due(&self, now: DateTime<Utc>, limit: usize) -> Result<Vec<Reminder>> {
        self.database
            .call(move |conn| {
                let tx = conn.transaction()?;
                let due: Vec<Reminder> = tx
                    .prepare(&format!("SELECT {COLUMNS} FROM reminders WHERE due_at <= ?1 ORDER BY due_at LIMIT ?2"))?
                    .query_map(params![format_time(now), limit as i64], from_row)?
                    .collect::<rusqlite::Result<_>>()?;
                for reminder in &due {
                    tx.execute("DELETE FROM reminders WHERE id = ?1", [reminder.id])?;
                }
                tx.commit()?;
                Ok(due)
            })
            .await
    }

    pub async fn delete_user(&self, user_id: u64) -> Result<()> {
        self.database
            .call(move |conn| conn.execute("DELETE FROM reminders WHERE user_id = ?1", [user_id as i64]).map(drop))
            .await
    }

    pub async fn delete_all(&self) -> Result<()> {
        self.database.call(|conn| conn.execute("DELETE FROM reminders", []).map(drop)).await
    }
}
