use thiserror::Error;

/// 預期內的應用程式錯誤。
#[derive(Debug, Error)]
pub enum EchoError {
    #[error("設定錯誤：{0}")]
    Config(String),

    #[error("儲存錯誤：{0}")]
    Storage(String),

    #[error("AI 服務錯誤：{0}")]
    AiService(String),

    /// AI 服務暫時過載（429／5xx），稍後重試通常即可恢復。
    #[error("AI 服務過載：{0}")]
    AiOverloaded(String),

    #[error("記憶服務錯誤：{0}")]
    MemoryService(String),

    #[error("Prompt 錯誤：{0}")]
    Prompt(String),
}

pub type Result<T> = std::result::Result<T, EchoError>;

impl From<rusqlite::Error> for EchoError {
    fn from(error: rusqlite::Error) -> Self {
        EchoError::Storage(error.to_string())
    }
}
