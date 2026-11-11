//! 單次對話請求的執行狀態，於主代理、子代理與工具之間共用。

use std::collections::{HashMap, HashSet};
use std::sync::Mutex;
use std::sync::atomic::{AtomicI64, Ordering};

use tokio::sync::mpsc::UnboundedSender;

use crate::safety::PromptGuard;
use crate::storage::CharacterState;

pub const MAX_REACTIONS: usize = 3;
pub const BUDGET_EXHAUSTED: &str = "本次對話的工具使用次數已達上限，請根據目前已取得的資訊直接回答。";

/// 代理執行過程中的事件，用於即時更新 Discord 訊息。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AgentEvent {
    /// 工具狀態或進度說明
    Status(String),
    /// 目前已生成的回覆文字（完整內容，非增量）
    Text(String),
    /// 重新執行，先前顯示的文字應清除
    Retry,
}

/// 訊息來源的 Discord 資訊，供需要頻道的工具（提醒、表情反應）使用。
///
/// 刻意不提供讀取頻道內容的能力：每位使用者的對話彼此隔離。
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct ChatOrigin {
    pub channel_id: Option<u64>,
    pub is_dm: bool,
}

pub struct RunContext {
    pub user_id: u64,
    pub guard: PromptGuard,
    pub origin: ChatOrigin,
    /// 模型輸出格式錯誤、只能取回純文字時沿用的角色狀態
    pub fallback_state: CharacterState,
    /// 使用者本次上傳的文件全文（file_id → 文字），供分段讀取
    pub attachments: HashMap<String, String>,
    tool_budget: AtomicI64,
    seen_urls: Mutex<HashSet<String>>,
    reactions: Mutex<Vec<String>>,
    events: Option<UnboundedSender<AgentEvent>>,
}

impl RunContext {
    pub fn new(user_id: u64, tool_budget: u32) -> Self {
        Self {
            user_id,
            guard: PromptGuard::new(),
            origin: ChatOrigin::default(),
            fallback_state: CharacterState::default(),
            attachments: HashMap::new(),
            tool_budget: AtomicI64::new(i64::from(tool_budget)),
            seen_urls: Mutex::new(HashSet::new()),
            reactions: Mutex::new(Vec::new()),
            events: None,
        }
    }

    pub fn with_events(mut self, events: UnboundedSender<AgentEvent>) -> Self {
        self.events = Some(events);
        self
    }

    /// 是否有人在接收事件（決定要不要串流）。
    pub fn has_listener(&self) -> bool {
        self.events.is_some()
    }

    /// 送出事件；接收端已關閉時直接忽略。
    pub fn emit(&self, event: AgentEvent) {
        if let Some(events) = &self.events {
            let _ = events.send(event);
        }
    }

    pub fn status(&self, text: impl Into<String>) {
        self.emit(AgentEvent::Status(text.into()));
    }

    /// 扣除一次工具額度；已用完時回傳 false。
    pub fn consume_budget(&self) -> bool {
        self.tool_budget.fetch_sub(1, Ordering::SeqCst) > 0
    }

    pub fn remaining_budget(&self) -> i64 {
        self.tool_budget.load(Ordering::SeqCst).max(0)
    }

    /// 記錄工具實際取得過的網址；回覆中的來源只能是這些網址。
    pub fn add_seen_url(&self, url: impl Into<String>) {
        self.seen_urls.lock().unwrap_or_else(|p| p.into_inner()).insert(url.into());
    }

    pub fn seen_urls(&self) -> HashSet<String> {
        self.seen_urls.lock().unwrap_or_else(|p| p.into_inner()).clone()
    }

    /// 加入表情反應；超過上限或重複時回傳 false。
    pub fn add_reaction(&self, emoji: &str) -> bool {
        let mut reactions = self.reactions.lock().unwrap_or_else(|p| p.into_inner());
        if reactions.len() >= MAX_REACTIONS {
            return false;
        }
        if !reactions.iter().any(|existing| existing == emoji) {
            reactions.push(emoji.to_owned());
        }
        true
    }

    pub fn reactions(&self) -> Vec<String> {
        self.reactions.lock().unwrap_or_else(|p| p.into_inner()).clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn budget_and_reactions() {
        let context = RunContext::new(1, 2);
        assert!(context.consume_budget() && context.consume_budget());
        assert!(!context.consume_budget());
        assert_eq!(context.remaining_budget(), 0);

        for emoji in ["❤️", "😆", "🥺"] {
            assert!(context.add_reaction(emoji));
        }
        assert!(!context.add_reaction("🎉"));
        assert_eq!(context.reactions().len(), 3);
    }

    #[test]
    fn events_are_delivered_when_listening() {
        let (sender, mut receiver) = tokio::sync::mpsc::unbounded_channel();
        let context = RunContext::new(1, 1).with_events(sender);
        context.status("▹ 搜尋");
        assert_eq!(receiver.try_recv().unwrap(), AgentEvent::Status("▹ 搜尋".into()));
        assert!(!RunContext::new(1, 1).has_listener());
    }
}
