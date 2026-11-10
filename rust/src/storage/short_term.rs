use rusqlite::params;

use super::Database;
use crate::error::Result;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
}

impl ChatMessage {
    pub fn new(role: impl Into<String>, content: impl Into<String>) -> Self {
        Self { role: role.into(), content: content.into() }
    }
}

/// 每位使用者最近的對話紀錄。
#[derive(Clone)]
pub struct ShortTermMemory {
    database: Database,
    max_messages: usize,
}

impl ShortTermMemory {
    pub fn new(database: Database, max_messages: usize) -> Self {
        Self { database, max_messages }
    }

    pub async fn add(&self, user_id: u64, messages: Vec<ChatMessage>) -> Result<()> {
        let limit = self.max_messages as i64;
        self.database
            .call(move |conn| {
                let tx = conn.transaction()?;
                for message in &messages {
                    tx.execute(
                        "INSERT INTO short_term_memory (user_id, role, content) VALUES (?1, ?2, ?3)",
                        params![user_id as i64, message.role, message.content],
                    )?;
                }
                tx.execute(
                    "DELETE FROM short_term_memory WHERE user_id = ?1 AND id NOT IN (
                         SELECT id FROM short_term_memory WHERE user_id = ?1 ORDER BY id DESC LIMIT ?2
                     )",
                    params![user_id as i64, limit],
                )?;
                tx.commit()
            })
            .await
    }

    pub async fn get(&self, user_id: u64) -> Result<Vec<ChatMessage>> {
        let limit = self.max_messages as i64;
        self.database
            .call(move |conn| {
                conn.prepare(
                    "SELECT role, content FROM (
                         SELECT id, role, content FROM short_term_memory
                         WHERE user_id = ?1 ORDER BY id DESC LIMIT ?2
                     ) ORDER BY id ASC",
                )?
                .query_map(params![user_id as i64, limit], |row| {
                    Ok(ChatMessage { role: row.get(0)?, content: row.get(1)? })
                })?
                .collect()
            })
            .await
    }

    pub async fn clear(&self, user_id: u64) -> Result<()> {
        self.database
            .call(move |conn| conn.execute("DELETE FROM short_term_memory WHERE user_id = ?1", [user_id as i64]).map(drop))
            .await
    }

    pub async fn clear_all(&self) -> Result<()> {
        self.database.call(|conn| conn.execute("DELETE FROM short_term_memory", []).map(drop)).await
    }
}
