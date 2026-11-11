//! OpenAI 相容的 Chat Completions API：請求組合、重試、節流與 SSE 串流解析。

use std::sync::Arc;
use std::time::Duration;

use serde_json::{Value, json};

use super::context::RunContext;
use super::throttle::{ApiThrottle, parse_retry_after};
use super::types::{Message, ToolCall};
use crate::config::Settings;
use crate::storage::TokenUsage;

/// 工具的定義，送給模型用。
#[derive(Debug, Clone, PartialEq)]
pub struct ToolSpec {
    pub name: String,
    pub description: String,
    /// 參數的 JSON schema
    pub parameters: Value,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ToolChoice {
    Auto,
    /// 禁止呼叫工具，只能直接作答
    None,
}

pub struct ChatRequest<'a> {
    pub model: &'a str,
    pub messages: &'a [Message],
    pub tools: &'a [ToolSpec],
    pub tool_choice: ToolChoice,
    /// 結構化輸出的 JSON schema；None 表示純文字
    pub response_schema: Option<&'a Value>,
    pub stream: bool,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Completion {
    pub content: String,
    pub tool_calls: Vec<ToolCall>,
    pub usage: TokenUsage,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum ApiError {
    /// 429 或 5xx，重試後仍失敗
    #[error("AI 服務過載或額度用盡：{0}")]
    Overloaded(String),
    /// 400 等請求本身被拒絕（例如模型不支援圖片）
    #[error("AI 服務拒絕請求：{0}")]
    Rejected(String),
    /// 連線失敗或逾時，重試後仍失敗
    #[error("無法連線到 AI 服務：{0}")]
    Transport(String),
    /// 回應內容無法解析
    #[error("AI 服務回應格式錯誤：{0}")]
    Malformed(String),
}

enum Attempt {
    Done(Completion),
    /// 可重試的失敗
    Retry(ApiError),
    Fatal(ApiError),
}

pub struct ChatApi {
    settings: Arc<Settings>,
    client: reqwest::Client,
    throttle: ApiThrottle,
    endpoint: String,
}

impl ChatApi {
    pub fn new(settings: Arc<Settings>) -> Self {
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(10))
            // 以「每次讀取」計時而非整體計時，長篇的串流回覆不會被中途切斷
            .read_timeout(Duration::from_secs_f64(settings.ai_timeout))
            .build()
            .expect("建立 AI 用的 HTTP 客戶端失敗");
        Self {
            throttle: ApiThrottle::new(
                settings.ai_requests_per_minute,
                Duration::from_secs_f64(settings.ai_rate_limit_cooldown),
            ),
            endpoint: format!("{}/chat/completions", settings.base_url.trim_end_matches('/')),
            client,
            settings,
        }
    }

    fn body(&self, request: &ChatRequest<'_>) -> Value {
        let mut body = json!({
            "model": request.model,
            "messages": request.messages.iter().map(Message::to_json).collect::<Vec<_>>(),
            "stream": request.stream,
        });
        if let Some(temperature) = self.settings.model_temperature {
            body["temperature"] = json!(temperature);
        }
        if let Some(max_tokens) = self.settings.max_output_tokens {
            body["max_tokens"] = json!(max_tokens);
        }
        if !request.tools.is_empty() {
            body["tools"] = request
                .tools
                .iter()
                .map(|tool| {
                    json!({
                        "type": "function",
                        "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
                    })
                })
                .collect();
            body["tool_choice"] = json!(match request.tool_choice {
                ToolChoice::Auto => "auto",
                ToolChoice::None => "none",
            });
            if request.tool_choice == ToolChoice::Auto {
                body["parallel_tool_calls"] = json!(true);
            }
        }
        if let Some(schema) = request.response_schema {
            body["response_format"] = json!({
                "type": "json_schema",
                "json_schema": {"name": "final_output", "strict": true, "schema": schema},
            });
        }
        // 非 OpenAI 官方的相容端點需明確要求，串流回應才會附上 token 用量
        if request.stream && self.settings.stream_include_usage {
            body["stream_options"] = json!({"include_usage": true});
        }
        body
    }

    /// 送出請求。429／5xx／連線錯誤會在節流冷卻後重試（最多 `AI_MAX_RETRIES` 次）。
    ///
    /// `on_delta` 在串流時收到每一段新增的文字；`on_restart` 在重試前呼叫，
    /// 讓呼叫端清掉已顯示的部分內容。
    pub async fn complete(
        &self,
        request: &ChatRequest<'_>,
        context: Option<&RunContext>,
        on_delta: &mut (dyn FnMut(&str) + Send),
        on_restart: &mut (dyn FnMut() + Send),
    ) -> Result<Completion, ApiError> {
        let body = self.body(request);
        let attempts = self.settings.ai_max_retries + 1;

        let mut last_error = ApiError::Transport("未送出請求".into());
        for attempt in 0..attempts {
            if attempt > 0 {
                on_restart();
                // 429 的等待由節流器負責；這裡只對其他錯誤做短暫退避
                tokio::time::sleep(Duration::from_millis(500 * (1 << (attempt - 1).min(4)))).await;
            }
            self.throttle.acquire(context).await;

            match self.attempt(&body, request.stream, on_delta).await {
                Attempt::Done(completion) => return Ok(completion),
                Attempt::Fatal(error) => return Err(error),
                Attempt::Retry(error) => {
                    tracing::warn!(attempt = attempt + 1, attempts, %error, model = request.model, "AI request failed");
                    last_error = error;
                }
            }
        }
        Err(last_error)
    }

    async fn attempt(&self, body: &Value, stream: bool, on_delta: &mut (dyn FnMut(&str) + Send)) -> Attempt {
        let response = match self.client.post(&self.endpoint).bearer_auth(&self.settings.api_key).json(body).send().await {
            Ok(response) => response,
            Err(error) => {
                let reason = if error.is_timeout() { "逾時" } else { "連線失敗" };
                return Attempt::Retry(ApiError::Transport(reason.into()));
            }
        };

        let status = response.status();
        if !status.is_success() {
            let retry_after = response.headers().get(reqwest::header::RETRY_AFTER).and_then(|v| v.to_str().ok()).map(str::to_owned);
            let detail: String = response.text().await.unwrap_or_default().chars().take(300).collect();
            let message = format!("HTTP {} {detail}", status.as_u16());
            return if status.as_u16() == 429 {
                self.throttle.rate_limited(parse_retry_after(retry_after.as_deref()));
                Attempt::Retry(ApiError::Overloaded(message))
            } else if status.is_server_error() {
                Attempt::Retry(ApiError::Overloaded(message))
            } else {
                Attempt::Fatal(ApiError::Rejected(message))
            };
        }

        if stream { read_stream(response, on_delta).await } else { read_json(response).await }
    }
}

fn usage_of(value: &Value) -> TokenUsage {
    let count = |key: &str| value.get("usage").and_then(|usage| usage.get(key)).and_then(Value::as_u64).unwrap_or(0);
    TokenUsage { requests: 1, input_tokens: count("prompt_tokens"), output_tokens: count("completion_tokens") }
}

async fn read_json(response: reqwest::Response) -> Attempt {
    let value: Value = match response.json().await {
        Ok(value) => value,
        Err(_) => return Attempt::Retry(ApiError::Malformed("回應不是有效的 JSON".into())),
    };
    let Some(message) = value.pointer("/choices/0/message") else {
        return Attempt::Fatal(ApiError::Malformed("回應缺少 choices".into()));
    };

    let tool_calls = message
        .get("tool_calls")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .enumerate()
        .filter_map(|(index, call)| {
            let function = call.get("function")?;
            Some(ToolCall {
                id: call.get("id").and_then(Value::as_str).map_or_else(|| format!("call_{index}"), str::to_owned),
                name: function.get("name")?.as_str()?.to_owned(),
                arguments: function.get("arguments").and_then(Value::as_str).unwrap_or("{}").to_owned(),
            })
        })
        .collect();

    Attempt::Done(Completion {
        content: message.get("content").and_then(Value::as_str).unwrap_or("").to_owned(),
        tool_calls,
        usage: usage_of(&value),
    })
}

/// 累積串流中的回應片段。
#[derive(Default)]
struct StreamState {
    content: String,
    /// 依 index 排列的工具呼叫（id、名稱、參數逐段累加）
    calls: Vec<ToolCall>,
    usage: Option<TokenUsage>,
}

impl StreamState {
    fn apply(&mut self, chunk: &Value, on_delta: &mut (dyn FnMut(&str) + Send)) {
        if chunk.get("usage").is_some_and(|usage| !usage.is_null()) {
            self.usage = Some(usage_of(chunk));
        }
        let Some(delta) = chunk.pointer("/choices/0/delta") else { return };

        if let Some(text) = delta.get("content").and_then(Value::as_str).filter(|text| !text.is_empty()) {
            self.content.push_str(text);
            on_delta(text);
        }
        for call in delta.get("tool_calls").and_then(Value::as_array).into_iter().flatten() {
            let index = call.get("index").and_then(Value::as_u64).unwrap_or(self.calls.len() as u64) as usize;
            while self.calls.len() <= index {
                self.calls.push(ToolCall { id: String::new(), name: String::new(), arguments: String::new() });
            }
            let entry = &mut self.calls[index];
            if let Some(id) = call.get("id").and_then(Value::as_str).filter(|id| !id.is_empty()) {
                entry.id = id.to_owned();
            }
            if let Some(function) = call.get("function") {
                if let Some(name) = function.get("name").and_then(Value::as_str) {
                    entry.name.push_str(name);
                }
                if let Some(arguments) = function.get("arguments").and_then(Value::as_str) {
                    entry.arguments.push_str(arguments);
                }
            }
        }
    }

    fn finish(self) -> Completion {
        let tool_calls = self
            .calls
            .into_iter()
            .enumerate()
            .filter(|(_, call)| !call.name.is_empty())
            .map(|(index, mut call)| {
                if call.id.is_empty() {
                    call.id = format!("call_{index}");
                }
                if call.arguments.trim().is_empty() {
                    call.arguments = "{}".into();
                }
                call
            })
            .collect();
        Completion {
            content: self.content,
            tool_calls,
            // 沒有回傳用量時仍計一次請求
            usage: self.usage.unwrap_or(TokenUsage { requests: 1, ..TokenUsage::default() }),
        }
    }
}

/// 把網路封包切成完整的行。
///
/// 以位元組緩衝並在換行處切開：一個中文字可能被拆在兩個封包裡，
/// 必須等整行到齊再解碼，否則會出現「���」亂碼。
#[derive(Default)]
struct LineBuffer {
    bytes: Vec<u8>,
}

impl LineBuffer {
    fn push(&mut self, chunk: &[u8]) -> Vec<String> {
        self.bytes.extend_from_slice(chunk);
        let mut lines = Vec::new();
        while let Some(newline) = self.bytes.iter().position(|byte| *byte == b'\n') {
            let line: Vec<u8> = self.bytes.drain(..=newline).collect();
            lines.push(String::from_utf8_lossy(&line).trim().to_owned());
        }
        lines
    }
}

async fn read_stream(mut response: reqwest::Response, on_delta: &mut (dyn FnMut(&str) + Send)) -> Attempt {
    let mut state = StreamState::default();
    let mut buffer = LineBuffer::default();

    loop {
        let chunk = match response.chunk().await {
            Ok(Some(chunk)) => chunk,
            Ok(None) => break,
            Err(_) if state.content.is_empty() && state.calls.is_empty() => {
                return Attempt::Retry(ApiError::Transport("串流中斷".into()));
            }
            // 已經收到部分內容時不重試，交給上層以現有內容處理
            Err(_) => break,
        };

        for line in buffer.push(&chunk) {
            let Some(data) = line.strip_prefix("data:").map(str::trim) else { continue };
            if data == "[DONE]" {
                return Attempt::Done(state.finish());
            }
            match serde_json::from_str::<Value>(data) {
                Ok(value) => state.apply(&value, on_delta),
                Err(_) => tracing::debug!(data, "ignored malformed stream chunk"),
            }
        }
    }
    Attempt::Done(state.finish())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn line_buffer_keeps_multibyte_characters_intact() {
        // 「使」的 UTF-8 是 3 個位元組，故意切在中間
        let line = "data: {\"text\": \"想對使用者\"}\n".as_bytes();
        let split = line.iter().position(|b| *b == 0xe4).unwrap() + 1;

        let mut buffer = LineBuffer::default();
        assert!(buffer.push(&line[..split]).is_empty());
        let lines = buffer.push(&line[split..]);
        assert_eq!(lines, ["data: {\"text\": \"想對使用者\"}"]);
        assert!(!lines[0].contains('\u{fffd}'));
    }

    #[test]
    fn stream_state_accumulates_text_and_tool_calls() {
        let mut state = StreamState::default();
        let mut seen = String::new();
        let mut on_delta = |delta: &str| seen.push_str(delta);

        for chunk in [
            json!({"choices": [{"delta": {"role": "assistant", "content": "你"}}]}),
            json!({"choices": [{"delta": {"content": "好"}}]}),
            json!({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "calc", "arguments": "{\"e\":"}}]}}]}),
            json!({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"1+1\"}"}}]}}]}),
            json!({"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3}}),
        ] {
            state.apply(&chunk, &mut on_delta);
        }
        let completion = state.finish();

        assert_eq!((completion.content.as_str(), seen.as_str()), ("你好", "你好"));
        assert_eq!(completion.tool_calls, [ToolCall { id: "c1".into(), name: "calc".into(), arguments: "{\"e\":\"1+1\"}".into() }]);
        assert_eq!(completion.usage, TokenUsage { requests: 1, input_tokens: 12, output_tokens: 3 });
    }
}
