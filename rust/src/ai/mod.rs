//! AI 客戶端：Chat Completions、工具呼叫迴圈、串流、結構化輸出與節流。

pub mod agent;
pub mod api;
pub mod client;
pub mod context;
pub mod output;
pub mod stream;
pub mod throttle;
pub mod types;

pub use agent::{Agent, Tool};
pub use client::{AiClient, RunOutcome};
pub use context::{AgentEvent, RunContext};
pub use types::{AiResponse, Message};
