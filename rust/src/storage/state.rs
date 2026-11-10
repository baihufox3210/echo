use chrono::{DateTime, NaiveDateTime, Utc};
use rusqlite::{OptionalExtension, params};
use serde::{Deserialize, Serialize};

use super::Database;
use crate::error::Result;

/// 角色對某位使用者的狀態，各項皆為 0～100。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct CharacterState {
    pub affection: u8,
    pub mood: u8,
    pub stamina: u8,
    pub trust: u8,
}

impl Default for CharacterState {
    fn default() -> Self {
        Self { affection: 0, mood: 50, stamina: 100, trust: 0 }
    }
}

impl CharacterState {
    /// 各項數值是否都在 0～100 之間（模型輸出需經此檢查）。
    pub fn is_valid(&self) -> bool {
        [self.affection, self.mood, self.stamina, self.trust].iter().all(|v| *v <= 100)
    }
}

#[derive(Clone)]
pub struct CharacterStateStore {
    database: Database,
}

impl CharacterStateStore {
    pub fn new(database: Database) -> Self {
        Self { database }
    }

    pub async fn get(&self, user_id: u64) -> Result<CharacterState> {
        let state = self
            .database
            .call(move |conn| {
                conn.query_row(
                    "SELECT affection, mood, stamina, trust FROM character_state WHERE user_id = ?1",
                    [user_id as i64],
                    |row| {
                        let clamp = |v: i64| v.clamp(0, 100) as u8;
                        Ok(CharacterState {
                            affection: clamp(row.get(0)?),
                            mood: clamp(row.get(1)?),
                            stamina: clamp(row.get(2)?),
                            trust: clamp(row.get(3)?),
                        })
                    },
                )
                .optional()
            })
            .await?;
        Ok(state.unwrap_or_default())
    }

    pub async fn save(&self, user_id: u64, state: CharacterState) -> Result<()> {
        self.database
            .call(move |conn| {
                conn.execute(
                    "INSERT INTO character_state
                         (user_id, affection, mood, stamina, trust, updated_at, last_interaction_at)
                     VALUES (?1, ?2, ?3, ?4, ?5, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                     ON CONFLICT(user_id) DO UPDATE SET
                         affection = excluded.affection,
                         mood = excluded.mood,
                         stamina = excluded.stamina,
                         trust = excluded.trust,
                         updated_at = excluded.updated_at,
                         last_interaction_at = excluded.last_interaction_at",
                    params![user_id as i64, state.affection, state.mood, state.stamina, state.trust],
                )
                .map(drop)
            })
            .await
    }

    pub async fn last_interaction(&self, user_id: u64) -> Result<Option<DateTime<Utc>>> {
        let raw: Option<Option<String>> = self
            .database
            .call(move |conn| {
                conn.query_row(
                    "SELECT COALESCE(last_interaction_at, updated_at) FROM character_state WHERE user_id = ?1",
                    [user_id as i64],
                    |row| row.get(0),
                )
                .optional()
            })
            .await?;
        Ok(raw
            .flatten()
            .and_then(|value| NaiveDateTime::parse_from_str(&value, "%Y-%m-%d %H:%M:%S").ok())
            .map(|naive| naive.and_utc()))
    }

    /// 長時間未互動的使用者每天降低好感度，回傳受影響的人數。
    pub async fn decay(&self, after_days: u32, amount: u32) -> Result<usize> {
        self.database
            .call(move |conn| {
                conn.execute(
                    "UPDATE character_state
                     SET affection = MAX(0, affection - ?1), last_decay_at = date('now')
                     WHERE affection > 0
                     AND julianday('now') - julianday(COALESCE(last_interaction_at, updated_at)) >= ?2
                     AND (last_decay_at IS NULL OR last_decay_at < date('now'))",
                    params![amount, after_days],
                )
            })
            .await
    }

    pub async fn delete_user(&self, user_id: u64) -> Result<()> {
        self.database
            .call(move |conn| conn.execute("DELETE FROM character_state WHERE user_id = ?1", [user_id as i64]).map(drop))
            .await
    }

    pub async fn delete_all(&self) -> Result<()> {
        self.database.call(|conn| conn.execute("DELETE FROM character_state", []).map(drop)).await
    }
}
