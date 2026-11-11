use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::storage::CharacterState;

/// 模型要求呼叫的工具。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    /// JSON 字串形式的參數
    pub arguments: String,
}

/// 送給 Chat Completions API 的訊息。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Message {
    System(String),
    /// `images` 為 data URL 或網址，交給支援視覺的模型
    User { text: String, images: Vec<String> },
    Assistant { content: Option<String>, tool_calls: Vec<ToolCall> },
    Tool { tool_call_id: String, content: String },
}

impl Message {
    pub fn system(text: impl Into<String>) -> Self {
        Self::System(text.into())
    }

    pub fn user(text: impl Into<String>) -> Self {
        Self::User { text: text.into(), images: Vec::new() }
    }

    pub fn assistant(text: impl Into<String>) -> Self {
        Self::Assistant { content: Some(text.into()), tool_calls: Vec::new() }
    }

    pub fn has_images(&self) -> bool {
        matches!(self, Self::User { images, .. } if !images.is_empty())
    }

    pub fn to_json(&self) -> Value {
        match self {
            Self::System(text) => json!({"role": "system", "content": text}),
            Self::User { text, images } if images.is_empty() => json!({"role": "user", "content": text}),
            Self::User { text, images } => {
                let mut parts = vec![json!({"type": "text", "text": text})];
                parts.extend(images.iter().map(|url| json!({"type": "image_url", "image_url": {"url": url}})));
                json!({"role": "user", "content": parts})
            }
            Self::Assistant { content, tool_calls } => {
                let mut message = json!({"role": "assistant", "content": content});
                if !tool_calls.is_empty() {
                    message["tool_calls"] = tool_calls
                        .iter()
                        .map(|call| {
                            json!({
                                "id": call.id,
                                "type": "function",
                                "function": {"name": call.name, "arguments": call.arguments},
                            })
                        })
                        .collect();
                }
                message
            }
            Self::Tool { tool_call_id, content } => {
                json!({"role": "tool", "tool_call_id": tool_call_id, "content": content})
            }
        }
    }
}

/// 模型每一輪的結構化回覆。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AiResponse {
    /// 角色對話：動作／神態描寫、『表面台詞』、（內心獨白）
    pub dialogue: String,
    /// 思緒摘要
    pub thoughts: Vec<String>,
    /// 行動推薦
    pub suggestions: Vec<String>,
    /// 本次互動結束後的角色狀態絕對值
    pub state: CharacterState,
    /// 本次回答實際引用的網址
    pub sources: Vec<String>,
}

impl AiResponse {
    /// 結構化輸出的 JSON schema（strict 模式）。
    pub fn json_schema() -> Value {
        let level = |description: &str| json!({"type": "integer", "minimum": 0, "maximum": 100, "description": description});
        let strings = |description: &str| json!({"type": "array", "items": {"type": "string"}, "description": description});
        json!({
            "type": "object",
            "additionalProperties": false,
            "required": ["dialogue", "thoughts", "suggestions", "state", "sources"],
            "properties": {
                "dialogue": {
                    "type": "string",
                    "description": "要直接傳送給使用者的角色對話，依序包含：動作／神態描寫、『表面台詞』、（內心獨白）。\
不要在此放入好感度標記、思緒摘要、行動推薦、人物狀態板或來源網址——那些另有欄位。\
若需摘要長篇內容（如檔案、網頁），請精簡重點，不要逐字照抄。",
                },
                "thoughts": strings("思緒摘要：她注意到什麼、最在意什麼、想做什麼、害怕什麼；無則給空陣列。"),
                "suggestions": strings("行動推薦：2～4 個可選行動，符合場景與好感度；不需要時給空陣列。"),
                "state": {
                    "type": "object",
                    "additionalProperties": false,
                    "required": ["affection", "mood", "stamina", "trust"],
                    "description": "本次互動結束後的角色狀態絕對值",
                    "properties": {
                        "affection": level("好感度 0~100"),
                        "mood": level("心情 0~100"),
                        "stamina": level("體力 0~100"),
                        "trust": level("信任度 0~100"),
                    },
                },
                "sources": strings("本次回答實際引用的網址（最多 5 個，未使用網路資料則為空陣列）"),
            },
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn message_json_shapes() {
        assert_eq!(Message::user("hi").to_json(), json!({"role": "user", "content": "hi"}));

        let with_image = Message::User { text: "看".into(), images: vec!["data:image/png;base64,AA".into()] };
        assert_eq!(with_image.to_json()["content"][1]["image_url"]["url"], "data:image/png;base64,AA");
        assert!(with_image.has_images());

        let call = Message::Assistant {
            content: None,
            tool_calls: vec![ToolCall { id: "c1".into(), name: "calc".into(), arguments: "{}".into() }],
        };
        let value = call.to_json();
        assert_eq!(value["tool_calls"][0]["function"]["name"], "calc");
        assert!(value["content"].is_null());
        assert!(Message::assistant("x").to_json().get("tool_calls").is_none());
    }

    #[test]
    fn schema_requires_every_field() {
        let schema = AiResponse::json_schema();
        assert_eq!(schema["required"].as_array().unwrap().len(), 5);
        assert_eq!(schema["properties"]["state"]["required"].as_array().unwrap().len(), 4);
    }
}
