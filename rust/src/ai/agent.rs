//! 工具呼叫迴圈：模型可連續、並行呼叫多個工具，直到資訊足夠再作答。

use std::sync::Arc;

use async_trait::async_trait;
use futures_util::future::join_all;
use serde_json::Value;

use super::api::{ApiError, ChatApi, ChatRequest, ToolChoice, ToolSpec};
use super::context::{AgentEvent, RunContext};
use super::stream::DialogueStream;
use super::types::{Message, ToolCall};
use crate::storage::TokenUsage;

const FINALIZE_PROMPT: &str = "（系統通知：工具使用回合已達上限。請不要再呼叫工具，直接根據上面已取得的資訊完成輸出。）";
const FINALIZE_STATUS: &str = "▹ 整理已蒐集的資訊";

/// 模型可以呼叫的工具。
#[async_trait]
pub trait Tool: Send + Sync {
    fn spec(&self) -> ToolSpec;

    /// 執行工具並回傳要交給模型的文字；失敗時回傳說明文字而不是錯誤，讓模型自行應對。
    async fn call(&self, context: &RunContext, arguments: Value) -> String;
}

pub struct Agent {
    pub name: String,
    /// 放在最前面的系統訊息（工具使用規則等）
    pub instructions: String,
    pub tools: Vec<Arc<dyn Tool>>,
    /// 最終輸出的 JSON schema；None 表示純文字
    pub response_schema: Option<Value>,
    pub max_turns: u32,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct AgentOutput {
    pub text: String,
    pub usage: TokenUsage,
    pub tools_used: Vec<String>,
}

impl Agent {
    /// 執行代理直到模型給出最終回覆。
    ///
    /// 回合數用盡時不直接失敗，而是禁止呼叫工具再請模型根據已取得的資訊作答。
    /// `stream` 為真且有結構化輸出時，會把逐步生成的對話文字以事件送出。
    pub async fn run(
        &self,
        api: &ChatApi,
        model: &str,
        input: &[Message],
        context: &RunContext,
        stream: bool,
    ) -> Result<AgentOutput, ApiError> {
        let specs: Vec<ToolSpec> = self.tools.iter().map(|tool| tool.spec()).collect();
        let mut messages = Vec::with_capacity(input.len() + 1);
        if !self.instructions.is_empty() {
            messages.push(Message::system(self.instructions.clone()));
        }
        messages.extend_from_slice(input);

        let mut output = AgentOutput::default();
        // 只有主代理（結構化輸出）的文字需要即時顯示
        let show_text = stream && self.response_schema.is_some();

        for turn in 0..=self.max_turns {
            let finalizing = turn == self.max_turns;
            if finalizing {
                tracing::info!(agent = %self.name, "max turns reached; finalizing without tools");
                context.status(FINALIZE_STATUS);
                messages.push(Message::system(FINALIZE_PROMPT));
            }

            let request = ChatRequest {
                model,
                messages: &messages,
                tools: &specs,
                tool_choice: if finalizing { ToolChoice::None } else { ToolChoice::Auto },
                response_schema: self.response_schema.as_ref(),
                stream,
            };

            let mut dialogue = DialogueStream::new();
            let mut on_delta = |delta: &str| {
                if show_text && let Some(text) = dialogue.feed(delta) {
                    context.emit(AgentEvent::Text(text));
                }
            };
            let mut on_restart = || {
                if show_text {
                    context.emit(AgentEvent::Retry);
                }
            };
            let completion = api.complete(&request, Some(context), &mut on_delta, &mut on_restart).await?;
            output.usage.add(completion.usage);

            if completion.tool_calls.is_empty() || finalizing {
                output.text = completion.content;
                return Ok(output);
            }

            output.tools_used.extend(completion.tool_calls.iter().map(|call| call.name.clone()));
            let results = join_all(completion.tool_calls.iter().map(|call| self.invoke(call, context))).await;

            messages.push(Message::Assistant {
                content: (!completion.content.is_empty()).then_some(completion.content),
                tool_calls: completion.tool_calls.clone(),
            });
            for (call, content) in completion.tool_calls.iter().zip(results) {
                messages.push(Message::Tool { tool_call_id: call.id.clone(), content });
            }
        }
        unreachable!("最後一輪必定回傳")
    }

    async fn invoke(&self, call: &ToolCall, context: &RunContext) -> String {
        let Some(tool) = self.tools.iter().find(|tool| tool.spec().name == call.name) else {
            return format!("沒有名為 {} 的工具。", call.name);
        };
        match serde_json::from_str::<Value>(&call.arguments) {
            Ok(arguments) if arguments.is_object() => tool.call(context, arguments).await,
            _ => "工具參數不是有效的 JSON 物件，請修正後再試。".to_owned(),
        }
    }
}
