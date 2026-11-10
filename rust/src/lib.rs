//! Echo：以 Discord 為介面的 AI 角色對話 Bot。
//!
//! 由 Python 版逐步改寫而來，進度見 `MIGRATION.md`。

pub mod concurrency;
pub mod documents;
pub mod config;
pub mod error;
pub mod safety;
pub mod search;
pub mod storage;
pub mod text;
