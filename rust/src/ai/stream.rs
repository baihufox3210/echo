//! 從串流中的不完整 JSON 取出目前可顯示的回覆文字。

use std::sync::LazyLock;

use regex::Regex;

static INCOMPLETE_ESCAPE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\\(u[0-9a-fA-F]{0,3})?$").unwrap());

/// 取出指定字串欄位目前已生成的內容。
pub struct PartialFieldExtractor {
    key: Regex,
    buffer: String,
}

impl PartialFieldExtractor {
    pub fn new(field: &str) -> Self {
        let key = Regex::new(&format!(r#""{}"\s*:\s*""#, regex::escape(field))).expect("欄位名稱應可組成正規表示式");
        Self { key, buffer: String::new() }
    }

    pub fn buffer(&self) -> &str {
        &self.buffer
    }

    pub fn reset(&mut self) {
        self.buffer.clear();
    }

    pub fn feed(&mut self, delta: &str) -> Option<String> {
        self.buffer.push_str(delta);
        self.value()
    }

    pub fn value(&self) -> Option<String> {
        let start = self.key.find(&self.buffer)?.end();
        let raw = &self.buffer[start..];
        let raw = match find_string_end(raw) {
            Some(end) => raw[..end].to_owned(),
            // 去掉尚未完整的跳脫序列
            None => INCOMPLETE_ESCAPE.replace(raw, "").into_owned(),
        };
        serde_json::from_str::<String>(&format!("\"{raw}\"")).ok()
    }
}

fn find_string_end(raw: &str) -> Option<usize> {
    let mut escaped = false;
    for (index, character) in raw.char_indices() {
        if escaped {
            escaped = false;
        } else if character == '\\' {
            escaped = true;
        } else if character == '"' {
            return Some(index);
        }
    }
    None
}

/// 取得串流中目前可顯示的回覆文字。
///
/// 正常情況下從 JSON 的 dialogue 欄位取出；若模型直接輸出純文字（未遵守 JSON 格式），
/// 則直接顯示累積的文字。
pub struct DialogueStream {
    extractor: PartialFieldExtractor,
}

const PLAIN_TEXT_THRESHOLD: usize = 8;

impl Default for DialogueStream {
    fn default() -> Self {
        Self { extractor: PartialFieldExtractor::new("dialogue") }
    }
}

impl DialogueStream {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn buffer(&self) -> &str {
        self.extractor.buffer()
    }

    pub fn reset(&mut self) {
        self.extractor.reset();
    }

    pub fn feed(&mut self, delta: &str) -> Option<String> {
        if let Some(value) = self.extractor.feed(delta) {
            return Some(value);
        }
        let head = self.extractor.buffer().trim_start();
        let looks_plain = head.chars().count() >= PLAIN_TEXT_THRESHOLD && !head.starts_with(['{', '`', '[']);
        looks_plain.then(|| head.to_owned())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_partial_dialogue() {
        let mut extractor = PartialFieldExtractor::new("dialogue");
        assert_eq!(extractor.feed(r#"{"dia"#), None);
        assert_eq!(extractor.feed(r#"logue": "你好\"#).as_deref(), Some("你好"));
        assert_eq!(extractor.feed(r"n世界\u00").as_deref(), Some("你好\n世界"));
        assert_eq!(extractor.feed(r#"41", "thoughts": []}"#).as_deref(), Some("你好\n世界A"));
    }

    #[test]
    fn dialogue_stream_handles_json_and_plain_text() {
        let mut stream = DialogueStream::new();
        assert_eq!(stream.feed(r#"{"dialogue": "你"#).as_deref(), Some("你"));
        stream.reset();
        assert_eq!(stream.feed("這是"), None);
        assert_eq!(stream.feed("一張可愛的貓咪照片").as_deref(), Some("這是一張可愛的貓咪照片"));
    }
}
