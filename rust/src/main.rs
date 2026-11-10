use anyhow::Context;
use echo::config::Settings;
use echo::storage::Database;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let _ = dotenvy::dotenv();
    let settings = Settings::from_env().context("設定載入失敗")?;

    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_new(settings.log_level.to_lowercase())
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let database = Database::open(&settings.memory_database_path).await?;
    tracing::info!(path = %settings.memory_database_path.display(), "database ready");
    drop(database);

    // Discord 連線與對話功能於後續階段加入（見 MIGRATION.md）
    tracing::warn!("Rust 版尚未完成，請繼續使用 Python 版執行 Bot");
    Ok(())
}
