//! 對話用的 AI 客戶端：執行主代理、解析輸出，並處理過載、備援模型與格式錯誤。

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};

use super::agent::{Agent, Tool};
use super::api::{ApiError, ChatApi};
use super::context::{AgentEvent, RunContext};
use super::output::{parse_response, salvage_response};
use super::types::{AiResponse, Message};
use crate::config::Settings;
use crate::error::{EchoError, Result};
use crate::storage::TokenUsage;

const IMAGE_UNREADABLE_NOTE: &str = "\n（附加的圖片無法讀取）";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RunOutcome {
    pub response: AiResponse,
    pub usage: TokenUsage,
    pub tools_used: Vec<String>,
}

pub struct AiClient {
    settings: Arc<Settings>,
    api: Arc<ChatApi>,
    agent: Agent,
    warned_missing_usage: AtomicBool,
}

impl AiClient {
    pub fn new(settings: Arc<Settings>, api: Arc<ChatApi>, instructions: String, tools: Vec<Arc<dyn Tool>>) -> Self {
        let agent = Agent {
            name: "Echo".into(),
            instructions,
            tools,
            response_schema: Some(AiResponse::json_schema()),
            max_turns: settings.ai_max_turns,
        };
        Self { settings, api, agent, warned_missing_usage: AtomicBool::new(false) }
    }

    pub fn api(&self) -> &Arc<ChatApi> {
        &self.api
    }

    /// 執行代理並回傳解析後的回覆。
    ///
    /// - 429／5xx／連線錯誤：API 層已重試並遵守節流冷卻，這裡不再重跑；
    ///   若設定了備援模型，過載時改用備援模型再試一次。
    /// - 模型輸出無法解析也無法救回：重新執行整個代理，最多 `AI_MAX_RUNS` 次。
    /// - 帶圖片的請求被拒絕：移除圖片後再試一次（模型不支援視覺）。
    pub async fn chat(&self, messages: &[Message], context: &RunContext) -> Result<RunOutcome> {
        let stream = self.settings.stream_responses && context.has_listener();
        let mut messages = messages.to_vec();
        let mut model = self.settings.model.as_str();
        let mut usage = TokenUsage::default();
        let mut tools_used: Vec<String> = Vec::new();
        let mut invalid_runs = 0;
        let mut stripped_images = false;

        loop {
            let output = match self.agent.run(&self.api, model, &messages, context, stream).await {
                Ok(output) => output,
                Err(ApiError::Overloaded(detail)) => {
                    tracing::warn!(model, %detail, "AI request overloaded after retries");
                    match self.settings.fallback_model.as_deref() {
                        Some(fallback) if model != fallback => {
                            tracing::warn!(fallback, "switching to fallback model");
                            model = fallback;
                            context.emit(AgentEvent::Retry);
                            continue;
                        }
                        _ => return Err(EchoError::AiOverloaded(detail)),
                    }
                }
                Err(ApiError::Rejected(detail)) if !stripped_images && messages.iter().any(Message::has_images) => {
                    tracing::warn!(%detail, "request with images rejected; retrying without images");
                    for message in &mut messages {
                        if let Message::User { text, images } = message
                            && !images.is_empty()
                        {
                            images.clear();
                            text.push_str(IMAGE_UNREADABLE_NOTE);
                        }
                    }
                    stripped_images = true;
                    context.emit(AgentEvent::Retry);
                    continue;
                }
                Err(error) => {
                    tracing::error!(model, %error, "AI request failed");
                    return Err(EchoError::AiService(error.to_string()));
                }
            };

            usage.add(output.usage);
            tools_used.extend(output.tools_used);

            if let Some(response) = self.parse(&output.text, context) {
                self.warn_if_usage_missing(usage, stream);
                return Ok(RunOutcome { response, usage, tools_used });
            }

            invalid_runs += 1;
            tracing::warn!(run = invalid_runs, max = self.settings.ai_max_runs, "model output unusable");
            if invalid_runs >= self.settings.ai_max_runs {
                return Err(EchoError::AiService("模型輸出無法解析".into()));
            }
            context.emit(AgentEvent::Retry);
        }
    }

    fn parse(&self, text: &str, context: &RunContext) -> Option<AiResponse> {
        if let Some(response) = parse_response(text) {
            return Some(response);
        }
        let preview: String = text.chars().take(300).collect();
        match salvage_response(text, context.fallback_state) {
            Some(response) => {
                tracing::warn!(raw = %preview, "model output did not match schema; salvaged it");
                Some(response)
            }
            None => {
                tracing::warn!(raw = %preview, "invalid model output could not be salvaged");
                None
            }
        }
    }

    fn warn_if_usage_missing(&self, usage: TokenUsage, stream: bool) {
        if usage.requests > 0 && usage.total_tokens() == 0 && !self.warned_missing_usage.swap(true, Ordering::Relaxed) {
            tracing::warn!(
                stream,
                "API returned no token usage; usage statistics will show 0. \
                 The endpoint may not support stream_options.include_usage."
            );
        }
    }
}
