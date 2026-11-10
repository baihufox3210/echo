//! SQLite 儲存層；資料表結構與 Python 版相同，可直接沿用既有資料庫。

mod notes;
mod reminders;
mod short_term;
mod state;
mod usage;

use std::path::Path;
use std::sync::{Arc, Mutex};

use rusqlite::Connection;

use crate::error::{EchoError, Result};

pub use notes::NoteStore;
pub use reminders::{Reminder, ReminderStore};
pub use short_term::{ChatMessage, ShortTermMemory};
pub use state::{CharacterState, CharacterStateStore};
pub use usage::{TokenUsage, UsageStore, UsageSummary};

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS short_term_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_short_term_memory_user ON short_term_memory(user_id, id);

CREATE TABLE IF NOT EXISTS character_state (
    user_id INTEGER PRIMARY KEY,
    affection INTEGER NOT NULL,
    mood INTEGER NOT NULL,
    stamina INTEGER NOT NULL,
    trust INTEGER NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS token_usage (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    messages INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    due_at TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_at);

CREATE TABLE IF NOT EXISTS user_notes (
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, key)
);
";

/// 既有資料庫需補上的欄位：(資料表, 欄位, 定義)
const COLUMN_MIGRATIONS: &[(&str, &str, &str)] = &[
    ("character_state", "last_interaction_at", "TIMESTAMP"),
    ("character_state", "last_decay_at", "TEXT"),
];

/// 共用的 SQLite 連線。查詢在阻塞執行緒池中執行，不會卡住非同步執行環境。
#[derive(Clone)]
pub struct Database {
    conn: Arc<Mutex<Connection>>,
}

impl Database {
    pub async fn open(path: &Path) -> Result<Self> {
        let path = path.to_owned();
        tokio::task::spawn_blocking(move || {
            if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
                std::fs::create_dir_all(parent).map_err(|e| EchoError::Storage(e.to_string()))?;
            }
            Self::initialize(Connection::open(path)?)
        })
        .await
        .map_err(|e| EchoError::Storage(e.to_string()))?
    }

    /// 記憶體資料庫，供測試使用。
    pub fn in_memory() -> Result<Self> {
        Self::initialize(Connection::open_in_memory()?)
    }

    fn initialize(conn: Connection) -> Result<Self> {
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "synchronous", "NORMAL")?;
        conn.busy_timeout(std::time::Duration::from_secs(5))?;
        conn.execute_batch(SCHEMA)?;

        for (table, column, definition) in COLUMN_MIGRATIONS {
            let exists = conn
                .prepare(&format!("PRAGMA table_info({table})"))?
                .query_map([], |row| row.get::<_, String>(1))?
                .filter_map(std::result::Result::ok)
                .any(|name| name == *column);
            if !exists {
                conn.execute(&format!("ALTER TABLE {table} ADD COLUMN {column} {definition}"), [])?;
            }
        }
        Ok(Self { conn: Arc::new(Mutex::new(conn)) })
    }

    /// 在阻塞執行緒中以獨占連線執行 `f`。
    pub async fn call<T, F>(&self, f: F) -> Result<T>
    where
        T: Send + 'static,
        F: FnOnce(&mut Connection) -> rusqlite::Result<T> + Send + 'static,
    {
        let conn = Arc::clone(&self.conn);
        tokio::task::spawn_blocking(move || {
            let mut guard = conn.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
            f(&mut guard).map_err(EchoError::from)
        })
        .await
        .map_err(|e| EchoError::Storage(e.to_string()))?
    }
}
