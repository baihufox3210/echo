//! 由環境變數（與 `.env`）載入設定；變數名稱與預設值和 Python 版一致。

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::str::FromStr;

use crate::error::{EchoError, Result};

#[derive(Debug, Clone)]
pub struct Settings {
    // Discord
    pub discord_token: String,
    pub respond_to_dm: bool,
    pub respond_to_replies: bool,
    pub sync_commands: bool,

    // AI
    pub base_url: String,
    pub api_key: String,
    pub model: String,
    /// 主要模型過載（429／5xx）時改用的備援模型
    pub fallback_model: Option<String>,
    pub model_temperature: Option<f64>,
    /// 單次回覆的最大輸出 token 數；過小會導致長回覆被截斷
    pub max_output_tokens: Option<u32>,
    pub ai_timeout: f64,
    pub ai_max_retries: u32,
    /// 所有 AI 請求合計的每分鐘上限，0 表示不限制
    pub ai_requests_per_minute: u32,
    /// 收到 429 且 API 未提供 Retry-After 時，所有 AI 請求暫停的秒數
    pub ai_rate_limit_cooldown: f64,
    pub ai_max_turns: u32,
    pub ai_max_runs: u32,
    pub tool_call_budget: u32,
    pub enable_research: bool,
    /// 以逗號分隔要停用的工具名稱
    pub disabled_tools: HashSet<String>,
    pub reminder_ping: bool,
    pub research_max_turns: u32,

    // 回覆呈現
    pub stream_responses: bool,
    pub stream_include_usage: bool,
    pub stream_edit_interval: f64,
    pub show_state_embed: bool,

    // 附件
    pub enable_vision: bool,
    pub max_attachments: usize,
    pub attachment_max_bytes: usize,
    pub attachment_inline_chars: usize,

    // 角色狀態自然衰減
    pub state_decay_enabled: bool,
    pub state_decay_after_days: u32,
    pub state_decay_amount: u32,

    pub healthcheck_file: PathBuf,

    // 併發與流量限制
    pub max_concurrent_requests: usize,
    pub rate_limit_per_user: usize,
    pub rate_limit_window: f64,
    pub max_message_length: usize,

    // 儲存
    pub memory_database_path: PathBuf,
    pub short_term_max_messages: usize,
    pub prompt_dir: PathBuf,

    // 長期記憶（未設定則停用）
    pub mem0_base_url: Option<String>,
    pub mem0_timeout: f64,
    pub mem0_top_k: usize,

    // 網路搜尋
    pub searxng_url: Option<String>,
    pub search_timeout: f64,
    pub search_max_results: usize,
    pub search_language: String,
    pub search_cache_ttl: f64,
    pub fetch_max_chars: usize,
    pub fetch_max_bytes: usize,
    pub fetch_allow_private: bool,

    pub timezone: String,
    pub log_level: String,
}

/// 讀取環境變數的小工具：空字串視為未設定。
struct Env(HashMap<String, String>);

impl Env {
    fn get(&self, key: &str) -> Option<&str> {
        self.0.get(key).map(|v| v.trim()).filter(|v| !v.is_empty())
    }

    fn required(&self, key: &str) -> Result<String> {
        self.get(key)
            .map(str::to_owned)
            .ok_or_else(|| EchoError::Config(format!("缺少必要的環境變數 {key}")))
    }

    fn string(&self, key: &str, default: &str) -> String {
        self.get(key).unwrap_or(default).to_owned()
    }

    fn optional<T: FromStr>(&self, key: &str) -> Result<Option<T>> {
        match self.get(key) {
            None => Ok(None),
            Some(raw) => raw
                .parse()
                .map(Some)
                .map_err(|_| EchoError::Config(format!("環境變數 {key} 的值無效：{raw}"))),
        }
    }

    fn parse<T: FromStr>(&self, key: &str, default: T) -> Result<T> {
        Ok(self.optional(key)?.unwrap_or(default))
    }

    fn flag(&self, key: &str, default: bool) -> Result<bool> {
        match self.get(key).map(str::to_ascii_lowercase).as_deref() {
            None => Ok(default),
            Some("1" | "true" | "yes" | "on") => Ok(true),
            Some("0" | "false" | "no" | "off") => Ok(false),
            Some(raw) => Err(EchoError::Config(format!("環境變數 {key} 應為 true 或 false：{raw}"))),
        }
    }

    fn url(&self, key: &str) -> Result<Option<String>> {
        match self.get(key) {
            None => Ok(None),
            Some(raw) if raw.starts_with("http://") || raw.starts_with("https://") => Ok(Some(raw.to_owned())),
            Some(raw) => Err(EchoError::Config(format!("環境變數 {key} 必須以 http:// 或 https:// 開頭：{raw}"))),
        }
    }
}

impl Settings {
    pub fn from_env() -> Result<Self> {
        Self::from_map(std::env::vars().collect())
    }

    pub fn from_map(vars: HashMap<String, String>) -> Result<Self> {
        let env = Env(vars);

        let base_url = env
            .url("BASE_URL")?
            .ok_or_else(|| EchoError::Config("缺少必要的環境變數 BASE_URL".into()))?;

        let disabled_tools = env
            .string("DISABLED_TOOLS", "")
            .split(',')
            .map(str::trim)
            .filter(|name| !name.is_empty())
            .map(str::to_owned)
            .collect();

        let settings = Self {
            discord_token: env.required("DISCORD_TOKEN")?,
            respond_to_dm: env.flag("RESPOND_TO_DM", true)?,
            respond_to_replies: env.flag("RESPOND_TO_REPLIES", true)?,
            sync_commands: env.flag("SYNC_COMMANDS", true)?,

            base_url,
            api_key: env.required("API_KEY")?,
            model: env.required("MODEL")?,
            fallback_model: env.get("FALLBACK_MODEL").map(str::to_owned),
            model_temperature: env.optional("MODEL_TEMPERATURE")?,
            max_output_tokens: Some(env.parse("MAX_OUTPUT_TOKENS", 4096)?),
            ai_timeout: env.parse("AI_TIMEOUT", 90.0)?,
            ai_max_retries: env.parse("AI_MAX_RETRIES", 2)?,
            ai_requests_per_minute: env.parse("AI_REQUESTS_PER_MINUTE", 0)?,
            ai_rate_limit_cooldown: env.parse("AI_RATE_LIMIT_COOLDOWN", 10.0)?,
            ai_max_turns: env.parse("AI_MAX_TURNS", 10)?,
            ai_max_runs: env.parse("AI_MAX_RUNS", 2)?,
            tool_call_budget: env.parse("TOOL_CALL_BUDGET", 24)?,
            enable_research: env.flag("ENABLE_RESEARCH", true)?,
            disabled_tools,
            reminder_ping: env.flag("REMINDER_PING", true)?,
            research_max_turns: env.parse("RESEARCH_MAX_TURNS", 12)?,

            stream_responses: env.flag("STREAM_RESPONSES", true)?,
            stream_include_usage: env.flag("STREAM_INCLUDE_USAGE", true)?,
            stream_edit_interval: env.parse("STREAM_EDIT_INTERVAL", 1.2)?,
            show_state_embed: env.flag("SHOW_STATE_EMBED", false)?,

            enable_vision: env.flag("ENABLE_VISION", true)?,
            max_attachments: env.parse("MAX_ATTACHMENTS", 4)?,
            attachment_max_bytes: env.parse("ATTACHMENT_MAX_BYTES", 8_000_000)?,
            attachment_inline_chars: env.parse("ATTACHMENT_INLINE_CHARS", 6_000)?,

            state_decay_enabled: env.flag("STATE_DECAY_ENABLED", true)?,
            state_decay_after_days: env.parse("STATE_DECAY_AFTER_DAYS", 3)?,
            state_decay_amount: env.parse("STATE_DECAY_AMOUNT", 1)?,

            healthcheck_file: PathBuf::from(env.string("HEALTHCHECK_FILE", "/tmp/echo-heartbeat")),

            max_concurrent_requests: env.parse("MAX_CONCURRENT_REQUESTS", 8)?,
            rate_limit_per_user: env.parse("RATE_LIMIT_PER_USER", 3)?,
            rate_limit_window: env.parse("RATE_LIMIT_WINDOW", 60.0)?,
            max_message_length: env.parse("MAX_MESSAGE_LENGTH", 4000)?,

            memory_database_path: PathBuf::from(env.string("MEMORY_DATABASE_PATH", "data/memory.db")),
            short_term_max_messages: env.parse("SHORT_TERM_MAX_MESSAGES", 20)?,
            prompt_dir: PathBuf::from(env.string("PROMPT_DIR", "prompt")),

            mem0_base_url: env.url("MEM0_BASE_URL")?,
            mem0_timeout: env.parse("MEM0_TIMEOUT", 60.0)?,
            mem0_top_k: env.parse("MEM0_TOP_K", 10)?,

            // 與 Python 版相同：未設定時預設本機的 SearXNG；要停用搜尋需明確設為空字串
            searxng_url: if env.0.contains_key("SEARXNG_URL") {
                env.url("SEARXNG_URL")?
            } else {
                Some("http://127.0.0.1:8080".to_owned())
            },
            search_timeout: env.parse("SEARCH_TIMEOUT", 20.0)?,
            search_max_results: env.parse("SEARCH_MAX_RESULTS", 5)?,
            search_language: env.string("SEARCH_LANGUAGE", "zh-TW"),
            search_cache_ttl: env.parse("SEARCH_CACHE_TTL", 300.0)?,
            fetch_max_chars: env.parse("FETCH_MAX_CHARS", 12_000)?,
            fetch_max_bytes: env.parse("FETCH_MAX_BYTES", 3_000_000)?,
            fetch_allow_private: env.flag("FETCH_ALLOW_PRIVATE", false)?,

            timezone: env.string("TIMEZONE", "Asia/Taipei"),
            log_level: env.string("LOG_LEVEL", "INFO"),
        };
        settings.validate()?;
        Ok(settings)
    }

    fn validate(&self) -> Result<()> {
        let checks = [
            (self.ai_max_turns >= 1, "AI_MAX_TURNS 至少為 1"),
            ((1..=5).contains(&self.ai_max_runs), "AI_MAX_RUNS 需介於 1～5"),
            (self.tool_call_budget >= 1, "TOOL_CALL_BUDGET 至少為 1"),
            (self.max_concurrent_requests >= 1, "MAX_CONCURRENT_REQUESTS 至少為 1"),
            (self.rate_limit_per_user >= 1, "RATE_LIMIT_PER_USER 至少為 1"),
            (self.rate_limit_window > 0.0, "RATE_LIMIT_WINDOW 必須大於 0"),
            (self.stream_edit_interval >= 0.5, "STREAM_EDIT_INTERVAL 至少為 0.5"),
            ((1..=20).contains(&self.search_max_results), "SEARCH_MAX_RESULTS 需介於 1～20"),
            (
                self.model_temperature.is_none_or(|t| (0.0..=2.0).contains(&t)),
                "MODEL_TEMPERATURE 需介於 0～2",
            ),
            (
                self.max_output_tokens.is_none_or(|t| t >= 256),
                "MAX_OUTPUT_TOKENS 至少為 256",
            ),
        ];
        match checks.iter().find(|(ok, _)| !ok) {
            Some((_, message)) => Err(EchoError::Config((*message).to_owned())),
            None => Ok(()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base() -> HashMap<String, String> {
        [
            ("DISCORD_TOKEN", "token"),
            ("BASE_URL", "http://localhost:1/v1"),
            ("API_KEY", "key"),
            ("MODEL", "model"),
        ]
        .into_iter()
        .map(|(k, v)| (k.to_owned(), v.to_owned()))
        .collect()
    }

    #[test]
    fn defaults_match_python_version() {
        let settings = Settings::from_map(base()).unwrap();
        assert_eq!(settings.ai_max_turns, 10);
        assert_eq!(settings.max_output_tokens, Some(4096));
        assert_eq!(settings.searxng_url.as_deref(), Some("http://127.0.0.1:8080"));
        assert!(settings.mem0_base_url.is_none());
        assert!(!settings.show_state_embed);
        assert!(settings.disabled_tools.is_empty());
    }

    #[test]
    fn parses_overrides_and_empty_values() {
        let mut vars = base();
        vars.insert("SEARXNG_URL".into(), "".into());
        vars.insert("DISABLED_TOOLS".into(), "get_weather, add_reaction".into());
        vars.insert("STREAM_RESPONSES".into(), "false".into());
        vars.insert("AI_REQUESTS_PER_MINUTE".into(), "12".into());
        let settings = Settings::from_map(vars).unwrap();

        assert!(settings.searxng_url.is_none());
        assert!(settings.disabled_tools.contains("add_reaction"));
        assert!(!settings.stream_responses);
        assert_eq!(settings.ai_requests_per_minute, 12);
    }

    #[test]
    fn rejects_missing_and_invalid_values() {
        let mut vars = base();
        vars.remove("MODEL");
        assert!(matches!(Settings::from_map(vars), Err(EchoError::Config(_))));

        let mut vars = base();
        vars.insert("BASE_URL".into(), "localhost".into());
        assert!(Settings::from_map(vars).is_err());

        let mut vars = base();
        vars.insert("AI_MAX_RUNS".into(), "9".into());
        assert!(Settings::from_map(vars).is_err());
    }
}
