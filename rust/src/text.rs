//! Discord 訊息文字處理。

pub const DISCORD_MESSAGE_LIMIT: usize = 2000;

/// 將過長文字切成多段（以字元數計），優先在段落、換行、句號、空白處切割。
pub fn split_message(text: &str, limit: usize) -> Vec<String> {
    let mut rest: Vec<char> = text.trim().chars().collect();
    let mut chunks = Vec::new();

    while rest.len() > limit {
        let window = &rest[..limit];
        let cut = ["\n\n", "\n", "。", " "]
            .iter()
            .find_map(|separator| {
                let pattern: Vec<char> = separator.chars().collect();
                rfind(window, &pattern)
                    .filter(|position| *position >= limit / 2)
                    .map(|position| position + pattern.len())
            })
            .unwrap_or(limit);

        let chunk: String = rest[..cut].iter().collect();
        chunks.push(chunk.trim_end().to_owned());
        let remaining: String = rest[cut..].iter().collect();
        rest = remaining.trim_start().chars().collect();
    }

    if !rest.is_empty() {
        chunks.push(rest.into_iter().collect());
    }
    chunks
}

fn rfind(haystack: &[char], needle: &[char]) -> Option<usize> {
    if needle.is_empty() || needle.len() > haystack.len() {
        return None;
    }
    (0..=haystack.len() - needle.len()).rev().find(|&start| &haystack[start..start + needle.len()] == needle)
}

/// 截斷為最多 `limit` 個字元並壓縮空白，超過時加上省略號。
pub fn truncate(text: &str, limit: usize) -> String {
    let collapsed = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if collapsed.chars().count() <= limit {
        return collapsed;
    }
    let mut cut: String = collapsed.chars().take(limit).collect();
    cut.truncate(cut.trim_end().len());
    cut.push('…');
    cut
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn short_message_is_single_chunk() {
        assert_eq!(split_message("hello", DISCORD_MESSAGE_LIMIT), vec!["hello"]);
        assert!(split_message("   ", DISCORD_MESSAGE_LIMIT).is_empty());
    }

    #[test]
    fn splits_on_newline_within_limit() {
        let text = vec!["a".repeat(30); 100].join("\n");
        let chunks = split_message(&text, 200);
        assert!(chunks.iter().all(|c| c.chars().count() <= 200));
        assert_eq!(chunks.join("\n"), text);
    }

    #[test]
    fn hard_cut_without_separator_counts_characters() {
        let chunks = split_message(&"字".repeat(4500), DISCORD_MESSAGE_LIMIT);
        assert_eq!(chunks.iter().map(|c| c.chars().count()).collect::<Vec<_>>(), vec![2000, 2000, 500]);
    }

    #[test]
    fn truncate_collapses_whitespace() {
        assert_eq!(truncate("a  b\n c", 10), "a b c");
        assert_eq!(truncate("一二三四五六", 3), "一二三…");
    }
}
