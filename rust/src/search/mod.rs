//! 聯網搜尋：文字比對、搜尋引擎、網頁抓取與站內查找。
//! 對應模型的工具（discover、web_search、explore_site、web_fetch）於第 4 階段加入。

pub mod common;
pub mod engine;
pub mod fetcher;
pub mod site;
pub mod text;

pub use engine::SearchEngine;
pub use fetcher::{FetchError, FetchedPage, PageFetcher};
pub use site::SiteExplorer;
