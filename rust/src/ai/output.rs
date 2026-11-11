//! 模型輸出的解析，以及未依照 JSON schema 輸出時的寬鬆救回。
//!
//! 部分 OpenAI 相容代理偶爾會以 ```json 程式碼框包裹、在 JSON 前後加上文字、
//! 直接回覆純文字，或因輸出長度上限而把 JSON 截斷。與其讓整個請求失敗，這裡盡量取回可用的內容。

use std::sync::LazyLock;

use regex::Regex;
use serde_json::Value;

use super::stream::PartialFieldExtractor;
use super::types::AiResponse;
use crate::storage::CharacterState;

static CODE_FENCE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)^```(?:json)?\s*|\s*```$").unwrap());

/// 嚴格解析：必須是完整且符合 schema 的 JSON，對話不可為空，狀態需在範圍內。
pub fn parse_response(text: &str) -> Option<AiResponse> {
    let response: AiResponse = serde_json::from_str(text.trim()).ok()?;
    (!response.dialogue.trim().is_empty() && response.state.is_valid()).then_some(response)
}

fn strip_fence(text: &str) -> String {
    CODE_FENCE.replace_all(text.trim(), "").into_owned()
}

fn extract_json_object(text: &str) -> Option<serde_json::Map<String, Value>> {
    let stripped = strip_fence(text);
    let mut candidates = vec![stripped.as_str()];
    if let (Some(start), Some(end)) = (stripped.find('{'), stripped.rfind('}'))
        && end > start
    {
        candidates.push(&stripped[start..=end]);
    }
    candidates.into_iter().find_map(|candidate| match serde_json::from_str::<Value>(candidate) {
        Ok(Value::Object(map)) => Some(map),
        _ => None,
    })
}

fn string_list(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .map(|items| {
            items.iter().filter_map(Value::as_str).filter(|s| !s.trim().is_empty()).map(str::to_owned).collect()
        })
        .unwrap_or_default()
}

fn from_object(data: &serde_json::Map<String, Value>, fallback_state: CharacterState) -> Option<AiResponse> {
    let dialogue = data.get("dialogue")?.as_str()?;
    if dialogue.trim().is_empty() {
        return None;
    }
    let state = data
        .get("state")
        .and_then(|value| serde_json::from_value::<CharacterState>(value.clone()).ok())
        .filter(CharacterState::is_valid)
        .unwrap_or(fallback_state);
    Some(AiResponse {
        dialogue: dialogue.to_owned(),
        thoughts: string_list(data.get("thoughts")),
        suggestions: string_list(data.get("suggestions")),
        state,
        sources: string_list(data.get("sources")),
    })
}

fn plain(dialogue: String, state: CharacterState) -> AiResponse {
    AiResponse { dialogue, thoughts: Vec::new(), suggestions: Vec::new(), state, sources: Vec::new() }
}

/// 盡量把不合格式的輸出轉為 [`AiResponse`]；完全沒有可用內容時回傳 None。
///
/// 依序嘗試：完整 JSON → 被截斷的 JSON 取出 dialogue 欄位 → 純文字直接當對話。
/// 救回時角色狀態維持不變（除非 JSON 內有合法的 state）。
pub fn salvage_response(text: &str, fallback_state: CharacterState) -> Option<AiResponse> {
    let text = text.trim();
    if text.is_empty() {
        return None;
    }

    if let Some(data) = extract_json_object(text) {
        // 是合法 JSON 但缺 dialogue，代表格式完全不符，不應把 JSON 原文當回覆
        return from_object(&data, fallback_state);
    }

    let stripped = strip_fence(text);
    // JSON 被截斷而無法解析時，仍嘗試取出已生成的 dialogue 片段
    if stripped.trim_start().starts_with('{') {
        return PartialFieldExtractor::new("dialogue")
            .feed(&stripped)
            .filter(|partial| !partial.trim().is_empty())
            .map(|partial| plain(partial, fallback_state));
    }

    // 模型直接回覆純文字：當作 dialogue 使用
    Some(plain(stripped, fallback_state))
}

#[cfg(test)]
mod tests {
    use super::*;

    const STATE: CharacterState = CharacterState { affection: 7, mood: 50, stamina: 80, trust: 3 };
    const VALID: &str = r#"{"dialogue": "你好～", "thoughts": [], "suggestions": [], "state": {"affection": 8, "mood": 55, "stamina": 80, "trust": 3}, "sources": []}"#;

    #[test]
    fn strict_parse() {
        assert_eq!(parse_response(VALID).unwrap().state.affection, 8);
        assert!(parse_response(r#"{"dialogue": "嗨"}"#).is_none());
        assert!(parse_response(&VALID.replace("\"affection\": 8", "\"affection\": 180")).is_none());
        assert!(parse_response(&VALID.replace("你好～", "  ")).is_none());
    }

    #[test]
    fn salvage_fenced_json() {
        let result = salvage_response(&format!("```json\n{VALID}\n```"), STATE).unwrap();
        assert_eq!((result.dialogue.as_str(), result.state.affection), ("你好～", 8));
    }

    #[test]
    fn salvage_json_with_surrounding_text_and_missing_fields() {
        let result = salvage_response(r#"好的，以下是回覆：{"dialogue": "嗨"} 希望有幫助"#, STATE).unwrap();
        assert_eq!(result.dialogue, "嗨");
        assert_eq!(result.state, STATE);
        assert!(result.sources.is_empty());
    }

    #[test]
    fn salvage_plain_text_keeps_state() {
        let result = salvage_response("這是一張貓咪的照片喔！", STATE).unwrap();
        assert_eq!((result.dialogue.as_str(), result.state), ("這是一張貓咪的照片喔！", STATE));
    }

    #[test]
    fn salvage_rejects_json_without_dialogue_and_empty() {
        assert!(salvage_response(r#"{"answer": "x"}"#, STATE).is_none());
        assert!(salvage_response("   ", STATE).is_none());
    }

    #[test]
    fn salvage_truncated_json_recovers_partial_dialogue() {
        // 模型因輸出上限而把 JSON 截斷在 dialogue 中途
        let truncated = "{\n  \"dialogue\": \"(看著檔案)\\n\\n『愛彌斯幫你整理好了：\\n1. 被動情蒐\\n2. 主動情蒐 (`-A`";
        let result = salvage_response(truncated, STATE).unwrap();
        assert!(result.dialogue.starts_with("(看著檔案)") && result.dialogue.contains("被動情蒐"));
        assert!(!result.dialogue.contains("dialogue") && !result.dialogue.contains('{'));
        assert_eq!(result.state, STATE);
    }
}
