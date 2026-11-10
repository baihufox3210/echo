# Rust 重構進度

Python 版（`src/echo/`）在 Rust 版功能齊全前維持為正式版本。
Rust 版沿用相同的 `.env` 變數、`prompt/` 目錄與 SQLite 資料表，切換時不需搬移資料。

| 階段 | 內容 | 狀態 |
| --- | --- | --- |
| 1 | 專案骨架、設定、儲存層（短期記憶、角色狀態、用量、筆記、提醒）、訊息分段、流量限制、提示詞注入防禦、搜尋文字比對 | 完成 |
| 2 | 聯網搜尋：SearXNG 客戶端、網頁抓取（SSRF 防護、robots、sitemap）、HTML／PDF／DOCX 解析、站內查找 | 未開始 |
| 3 | AI 客戶端：Chat Completions、工具呼叫迴圈、串流、結構化輸出與救回、節流與備援模型 | 未開始 |
| 4 | 工具：時間、計算、擲骰、天氣、維基百科、匯率、筆記、提醒、附件、表情反應、研究子代理；對話服務（Prompt 組合、記憶、洩漏防護） | 未開始 |
| 5 | Discord：訊息處理、串流回覆、Embed、斜線指令、提醒派送、好感度衰減、健康檢查 | 未開始 |
| 6 | Dockerfile 與 compose 切換為 Rust 版、移除 Python 版、更新 README | 未開始 |

## 對應關係

| Python | Rust |
| --- | --- |
| `core/config.py` | `config.rs` |
| `core/errors.py` | `error.rs` |
| `core/text.py` | `text.rs` |
| `core/concurrency.py` | `concurrency.rs` |
| `storage/*.py`、`memory/short_term.py` | `storage/` |
| `ai/safety.py` | `safety.rs` |
| `ai/search/text.py` | `search/text.rs` |

## 開發

```bash
cd rust
cargo test
cargo clippy --all-targets -- -D warnings
```
