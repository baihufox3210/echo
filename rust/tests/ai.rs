//! 以本機的模擬 Chat Completions 端點測試代理迴圈與客戶端（對應 Python 版 tests/test_client.py、test_throttle.py）。

use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use async_trait::async_trait;
use echo::ai::api::{ChatApi, ToolSpec};
use echo::ai::{AgentEvent, AiClient, Message, RunContext, Tool};
use echo::config::Settings;
use echo::error::EchoError;
use echo::storage::CharacterState;
use serde_json::{Value, json};
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, Request, ResponseTemplate};

const STATE: CharacterState = CharacterState { affection: 7, mood: 50, stamina: 80, trust: 3 };

fn valid_reply() -> String {
    json!({"dialogue": "你好～", "thoughts": ["開心"], "suggestions": [], "state": {"affection": 8, "mood": 55, "stamina": 80, "trust": 3}, "sources": []})
        .to_string()
}

fn settings(server: &MockServer, overrides: &[(&str, &str)]) -> Arc<Settings> {
    let mut vars: HashMap<String, String> = [
        ("DISCORD_TOKEN", "t"),
        ("API_KEY", "k"),
        ("MODEL", "main-model"),
        ("AI_RATE_LIMIT_COOLDOWN", "0.1"),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_owned(), v.to_owned()))
    .collect();
    vars.insert("BASE_URL".into(), format!("{}/v1", server.uri()));
    for (key, value) in overrides {
        vars.insert((*key).to_owned(), (*value).to_owned());
    }
    Arc::new(Settings::from_map(vars).unwrap())
}

fn completion(message: Value) -> ResponseTemplate {
    ResponseTemplate::new(200).set_body_json(json!({
        "id": "x", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }))
}

fn text_reply(content: &str) -> ResponseTemplate {
    completion(json!({"role": "assistant", "content": content}))
}

fn tool_call_reply(name: &str, arguments: Value) -> ResponseTemplate {
    completion(json!({
        "role": "assistant", "content": null,
        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": name, "arguments": arguments.to_string()}}],
    }))
}

fn sse(chunks: &[Value]) -> ResponseTemplate {
    let mut body: String = chunks.iter().map(|chunk| format!("data: {chunk}\n\n")).collect();
    body.push_str("data: [DONE]\n\n");
    ResponseTemplate::new(200).set_body_raw(body, "text/event-stream")
}

fn sse_text(text: &str, with_usage: bool) -> ResponseTemplate {
    let characters: Vec<char> = text.chars().collect();
    let mut chunks: Vec<Value> = characters
        .chunks(5)
        .map(|part| json!({"choices": [{"index": 0, "delta": {"content": part.iter().collect::<String>()}}]}))
        .collect();
    chunks.push(json!({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}));
    if with_usage {
        chunks.push(json!({"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 30}}));
    }
    sse(&chunks)
}

/// 掛上依請求內容與第幾次呼叫決定回應的處理函式。
async fn serve(server: &MockServer, respond: impl Fn(usize, &Value) -> ResponseTemplate + Send + Sync + 'static) {
    let calls = AtomicUsize::new(0);
    Mock::given(method("POST"))
        .and(path("/v1/chat/completions"))
        .respond_with(move |request: &Request| {
            let body: Value = serde_json::from_slice(&request.body).unwrap();
            respond(calls.fetch_add(1, Ordering::SeqCst), &body)
        })
        .mount(server)
        .await;
}

async fn requests(server: &MockServer) -> Vec<Value> {
    server.received_requests().await.unwrap().iter().map(|r| serde_json::from_slice(&r.body).unwrap()).collect()
}

/// 記錄收到的參數並回傳固定結果的測試工具。
struct Echo {
    seen: Arc<Mutex<Vec<Value>>>,
}

#[async_trait]
impl Tool for Echo {
    fn spec(&self) -> ToolSpec {
        ToolSpec {
            name: "lookup".into(),
            description: "查詢".into(),
            parameters: json!({"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"], "additionalProperties": false}),
        }
    }

    async fn call(&self, context: &RunContext, arguments: Value) -> String {
        context.status("▹ 查詢中");
        self.seen.lock().unwrap().push(arguments);
        "查詢結果：42".into()
    }
}

fn make_client(settings: Arc<Settings>) -> (AiClient, Arc<Mutex<Vec<Value>>>) {
    let seen = Arc::new(Mutex::new(Vec::new()));
    let tools: Vec<Arc<dyn Tool>> = vec![Arc::new(Echo { seen: Arc::clone(&seen) })];
    let api = Arc::new(ChatApi::new(Arc::clone(&settings)));
    (AiClient::new(settings, api, "工具規則".into(), tools), seen)
}

fn context() -> RunContext {
    let mut context = RunContext::new(1, 5);
    context.fallback_state = STATE;
    context
}

fn listening() -> (RunContext, tokio::sync::mpsc::UnboundedReceiver<AgentEvent>) {
    let (sender, receiver) = tokio::sync::mpsc::unbounded_channel();
    let mut context = RunContext::new(1, 5).with_events(sender);
    context.fallback_state = STATE;
    (context, receiver)
}

fn drain(receiver: &mut tokio::sync::mpsc::UnboundedReceiver<AgentEvent>) -> Vec<AgentEvent> {
    std::iter::from_fn(|| receiver.try_recv().ok()).collect()
}

fn input() -> Vec<Message> {
    vec![Message::system("角色設定"), Message::user("這是什麼")]
}

#[tokio::test]
async fn runs_tool_then_parses_structured_reply() {
    let server = MockServer::start().await;
    serve(&server, |call, _| match call {
        0 => tool_call_reply("lookup", json!({"q": "答案"})),
        _ => text_reply(&valid_reply()),
    })
    .await;
    let (client, seen) = make_client(settings(&server, &[]));

    let outcome = client.chat(&input(), &context()).await.unwrap();

    assert_eq!(outcome.response.dialogue, "你好～");
    assert_eq!(outcome.response.state.affection, 8);
    assert_eq!(outcome.tools_used, ["lookup"]);
    assert_eq!((outcome.usage.requests, outcome.usage.input_tokens, outcome.usage.output_tokens), (2, 20, 10));
    assert_eq!(*seen.lock().unwrap(), [json!({"q": "答案"})]);

    let sent = requests(&server).await;
    let first = &sent[0];
    assert_eq!(first["model"], "main-model");
    assert_eq!(first["max_tokens"], 4096);
    assert_eq!(first["messages"][0], json!({"role": "system", "content": "工具規則"}));
    assert_eq!(first["tools"][0]["function"]["name"], "lookup");
    assert_eq!(first["response_format"]["json_schema"]["strict"], true);
    assert_eq!(first["parallel_tool_calls"], true);
    // 第二次請求帶著工具呼叫與結果
    let roles: Vec<&str> = sent[1]["messages"].as_array().unwrap().iter().map(|m| m["role"].as_str().unwrap()).collect();
    assert_eq!(roles, ["system", "system", "user", "assistant", "tool"]);
    assert_eq!(sent[1]["messages"][4]["content"], "查詢結果：42");
}

#[tokio::test]
async fn streams_dialogue_text_and_records_usage() {
    let server = MockServer::start().await;
    serve(&server, |_, _| sse_text(&valid_reply(), true)).await;
    let (client, _) = make_client(settings(&server, &[]));
    let (context, mut receiver) = listening();

    let outcome = client.chat(&input(), &context).await.unwrap();

    assert_eq!(outcome.response.dialogue, "你好～");
    assert_eq!((outcome.usage.requests, outcome.usage.input_tokens, outcome.usage.output_tokens), (1, 120, 30));
    let texts: Vec<String> = drain(&mut receiver)
        .into_iter()
        .filter_map(|event| if let AgentEvent::Text(text) = event { Some(text) } else { None })
        .collect();
    assert!(texts.len() > 1, "應逐步送出文字：{texts:?}");
    assert_eq!(texts.last().unwrap(), "你好～");

    let sent = requests(&server).await;
    assert_eq!(sent[0]["stream"], true);
    assert_eq!(sent[0]["stream_options"], json!({"include_usage": true}));
}

#[tokio::test]
async fn streamed_tool_call_arguments_are_assembled() {
    let server = MockServer::start().await;
    serve(&server, |call, _| match call {
        0 => sse(&[
            json!({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c9", "function": {"name": "lookup", "arguments": "{\"q\":"}}]}}]}),
            json!({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": " \"課程\"}"}}]}}]}),
        ]),
        _ => sse_text(&valid_reply(), false),
    })
    .await;
    let (client, seen) = make_client(settings(&server, &[]));
    let (context, mut receiver) = listening();

    let outcome = client.chat(&input(), &context).await.unwrap();

    assert_eq!(*seen.lock().unwrap(), [json!({"q": "課程"})]);
    assert_eq!(outcome.tools_used, ["lookup"]);
    assert!(drain(&mut receiver).contains(&AgentEvent::Status("▹ 查詢中".into())));
    assert_eq!(requests(&server).await[1]["messages"][3]["tool_calls"][0]["id"], "c9");
}

#[tokio::test]
async fn finalizes_without_tools_when_turns_run_out() {
    let server = MockServer::start().await;
    serve(&server, |_, body| {
        if body["tool_choice"] == "none" { text_reply(&valid_reply()) } else { tool_call_reply("lookup", json!({"q": "再查"})) }
    })
    .await;
    let (client, seen) = make_client(settings(&server, &[("AI_MAX_TURNS", "2")]));

    let outcome = client.chat(&input(), &context()).await.unwrap();

    assert_eq!(outcome.response.dialogue, "你好～");
    assert_eq!(seen.lock().unwrap().len(), 2);
    assert_eq!(outcome.usage.requests, 3);
    let sent = requests(&server).await;
    assert_eq!(sent.last().unwrap()["tool_choice"], "none");
    assert!(sent.last().unwrap().get("parallel_tool_calls").is_none());
}

#[tokio::test]
async fn plain_text_and_fenced_output_are_salvaged() {
    for (reply, expected) in [
        ("這是一張貓咪的照片喔！".to_owned(), "這是一張貓咪的照片喔！"),
        (format!("```json\n{}\n```", valid_reply()), "你好～"),
    ] {
        let server = MockServer::start().await;
        let reply_for_server = reply.clone();
        serve(&server, move |_, _| text_reply(&reply_for_server)).await;
        let (client, _) = make_client(settings(&server, &[]));

        let outcome = client.chat(&input(), &context()).await.unwrap();
        assert_eq!(outcome.response.dialogue, expected);
        assert_eq!(requests(&server).await.len(), 1);
    }
}

#[tokio::test]
async fn unusable_output_is_rerun_then_fails() {
    let server = MockServer::start().await;
    serve(&server, |call, _| match call {
        0 => text_reply(r#"{"answer": "沒有 dialogue"}"#),
        _ => text_reply(&valid_reply()),
    })
    .await;
    let (client, _) = make_client(settings(&server, &[]));
    assert_eq!(client.chat(&input(), &context()).await.unwrap().response.dialogue, "你好～");

    let server = MockServer::start().await;
    serve(&server, |_, _| text_reply("   ")).await;
    let (client, _) = make_client(settings(&server, &[]));
    assert!(matches!(client.chat(&input(), &context()).await, Err(EchoError::AiService(_))));
    assert_eq!(requests(&server).await.len(), 2); // AI_MAX_RUNS 預設 2
}

#[tokio::test]
async fn rate_limit_is_retried_with_cooldown_not_multiplied() {
    let server = MockServer::start().await;
    let times = Arc::new(Mutex::new(Vec::new()));
    let recorded = Arc::clone(&times);
    serve(&server, move |_, _| {
        recorded.lock().unwrap().push(Instant::now());
        ResponseTemplate::new(429).set_body_json(json!({"error": {"message": "quota"}}))
    })
    .await;
    let (client, _) = make_client(settings(&server, &[]));

    let result = client.chat(&input(), &context()).await;

    assert!(matches!(result, Err(EchoError::AiOverloaded(_))));
    // 只有 API 層的重試（預設共 3 次），外層不再重跑整個代理
    let times = times.lock().unwrap();
    assert_eq!(times.len(), 3);
    assert!(times.windows(2).all(|pair| pair[1].duration_since(pair[0]).as_millis() >= 90));
}

#[tokio::test]
async fn retry_after_header_is_honoured_and_recovers() {
    let server = MockServer::start().await;
    serve(&server, |call, _| match call {
        0 => ResponseTemplate::new(429).insert_header("retry-after", "1"),
        _ => text_reply(&valid_reply()),
    })
    .await;
    let (client, _) = make_client(settings(&server, &[]));

    let start = Instant::now();
    let outcome = client.chat(&input(), &context()).await.unwrap();
    assert_eq!(outcome.response.dialogue, "你好～");
    assert!(start.elapsed().as_millis() >= 900, "應等待 Retry-After 指定的時間");
}

#[tokio::test]
async fn overload_switches_to_fallback_model() {
    let server = MockServer::start().await;
    serve(&server, |_, body| {
        if body["model"] == "main-model" { ResponseTemplate::new(503) } else { text_reply(&valid_reply()) }
    })
    .await;
    let (client, _) = make_client(settings(&server, &[("FALLBACK_MODEL", "backup-model"), ("AI_MAX_RETRIES", "0")]));

    let outcome = client.chat(&input(), &context()).await.unwrap();

    assert_eq!(outcome.response.dialogue, "你好～");
    let models: Vec<String> = requests(&server).await.iter().map(|r| r["model"].as_str().unwrap().to_owned()).collect();
    assert_eq!(models, ["main-model", "backup-model"]);
}

#[tokio::test]
async fn rejected_images_are_retried_without_images() {
    let server = MockServer::start().await;
    serve(&server, |_, body| {
        let has_image = body["messages"].as_array().unwrap().iter().any(|m| m["content"].is_array());
        if has_image { ResponseTemplate::new(400).set_body_string("images not supported") } else { text_reply(&valid_reply()) }
    })
    .await;
    let (client, _) = make_client(settings(&server, &[]));
    let messages = vec![Message::User { text: "看這張".into(), images: vec!["data:image/png;base64,AA".into()] }];

    let outcome = client.chat(&messages, &context()).await.unwrap();

    assert_eq!(outcome.response.dialogue, "你好～");
    let sent = requests(&server).await;
    assert_eq!(sent.len(), 2);
    assert!(sent[1]["messages"][1]["content"].as_str().unwrap().contains("圖片無法讀取"));
}

#[tokio::test]
async fn bad_request_without_images_fails_immediately() {
    let server = MockServer::start().await;
    serve(&server, |_, _| ResponseTemplate::new(400).set_body_string("bad")).await;
    let (client, _) = make_client(settings(&server, &[]));

    assert!(matches!(client.chat(&input(), &context()).await, Err(EchoError::AiService(_))));
    assert_eq!(requests(&server).await.len(), 1);
}
