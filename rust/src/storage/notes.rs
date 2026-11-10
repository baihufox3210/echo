use rusqlite::params;

use super::Database;
use crate::error::Result;

/// AI 主動記下的使用者重點（喜好、生日、稱呼等），每次對話都會提供給 AI。
#[derive(Clone)]
pub struct NoteStore {
    database: Database,
}

impl NoteStore {
    pub fn new(database: Database) -> Self {
        Self { database }
    }

    pub async fn set(&self, user_id: u64, key: String, value: String) -> Result<()> {
        self.database
            .call(move |conn| {
                conn.execute(
                    "INSERT INTO user_notes (user_id, key, value, updated_at)
                     VALUES (?1, ?2, ?3, CURRENT_TIMESTAMP)
                     ON CONFLICT(user_id, key) DO UPDATE SET
                         value = excluded.value, updated_at = excluded.updated_at",
                    params![user_id as i64, key, value],
                )
                .map(drop)
            })
            .await
    }

    /// 依最後更新時間排序的 (項目, 內容)。
    pub async fn get_all(&self, user_id: u64) -> Result<Vec<(String, String)>> {
        self.database
            .call(move |conn| {
                conn.prepare("SELECT key, value FROM user_notes WHERE user_id = ?1 ORDER BY updated_at, rowid")?
                    .query_map([user_id as i64], |row| Ok((row.get(0)?, row.get(1)?)))?
                    .collect()
            })
            .await
    }

    pub async fn delete(&self, user_id: u64, key: String) -> Result<bool> {
        self.database
            .call(move |conn| {
                conn.execute("DELETE FROM user_notes WHERE user_id = ?1 AND key = ?2", params![user_id as i64, key])
                    .map(|rows| rows > 0)
            })
            .await
    }

    pub async fn delete_user(&self, user_id: u64) -> Result<()> {
        self.database
            .call(move |conn| conn.execute("DELETE FROM user_notes WHERE user_id = ?1", [user_id as i64]).map(drop))
            .await
    }

    pub async fn delete_all(&self) -> Result<()> {
        self.database.call(|conn| conn.execute("DELETE FROM user_notes", []).map(drop)).await
    }
}
