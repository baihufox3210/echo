# Echo

Echo 是一個以 Discord 為介面的 AI 角色對話 Bot。使用者提及 Bot 時即可開始聊天；Echo 會結合可自訂的角色 Prompt、短期對話記錄、長期記憶與角色狀態，延續個人化互動。AI 也可查詢目前時間、透過 SearXNG 搜尋網路，以及讀取網頁內容。

## 功能

- **Discord 對話**：提及 Bot 即可觸發回覆，並限制每位使用者每分鐘最多 3 次請求。
- **短期記憶**：以 SQLite 保存每位使用者最近 20 則訊息。
- **長期記憶**：透過 Mem0-compatible API 搜尋並保存對話記憶。
- **角色狀態**：以 JSON 保存每位使用者的好感度、心情、體力與信任度。
- **可編輯 Prompt**：角色、互動規則、世界觀與狀態描述皆以 Markdown 檔案管理。
- **AI 工具**：提供台灣時區時間、SearXNG 網路搜尋與網頁內容擷取。
- **記憶管理**：使用者可刪除自己的資料；Bot owner 可刪除所有使用者資料。

## 需求

- [Python](https://www.python.org/) 3.15 或以上
- [uv](https://docs.astral.sh/uv/)
- Discord Bot 應用程式
- OpenAI-compatible Chat Completions API
- Mem0-compatible 記憶服務
- [SearXNG](https://docs.searxng.org/)（僅在需要網路搜尋時使用）

## 安裝與設定

在專案根目錄安裝相依套件：

```bash
uv sync
```

在 Discord Developer Portal 建立 Bot，取得 Bot Token，並在 Bot 設定中啟用 **Message Content Intent**。邀請 Bot 加入伺服器時，選取 `bot` 與 `applications.commands` scopes，並授予讀取及傳送訊息所需的權限。

在專案根目錄建立 `.env`：

```env
DISCORD_TOKEN=your_discord_bot_token
BASE_URL=https://api.example.com/v1
API_KEY=your_api_key
MODEL=your_model
MEM0_BASE_URL=http://localhost:8888
```

必要設定：

| 變數 | 說明 |
| --- | --- |
| `DISCORD_TOKEN` | Discord Bot Token |
| `BASE_URL` | OpenAI-compatible API 的 Base URL |
| `API_KEY` | AI API 金鑰 |
| `MODEL` | 要使用的模型名稱 |
| `MEM0_BASE_URL` | Mem0-compatible 記憶服務的 Base URL；服務需支援 `/memories` 與 `/search` API |

以下設定可省略；括號中為預設值：

| 變數 | 說明 |
| --- | --- |
| `MEMORY_DATABASE_PATH` (`data/memory.db`) | SQLite 短期記憶資料庫路徑 |
| `CHARACTER_STATE_PATH` (`data/character_state.json`) | 角色狀態 JSON 檔案路徑 |
| `PROMPT_DIR` (`prompt`) | Prompt 檔案目錄 |
| `SEARXNG_URL` (`http://127.0.0.1:8080`) | SearXNG 服務網址 |
| `SEARCH_TIMEOUT` (`20.0`) | 搜尋與網頁擷取逾時秒數 |
| `SEARCH_MAX_RESULTS` (`5`) | 每次搜尋最多回傳的結果數 |
| `SEARCH_LANGUAGE` (`zh-TW`) | 搜尋語言設定 |

`BASE_URL` 與 `MEM0_BASE_URL` 必須以 `http://` 或 `https://` 開頭。若不使用網路搜尋，仍可省略 SearXNG 服務；但需要查詢最新資訊時，請先啟動 SearXNG 並設定正確的 `SEARXNG_URL`。

## 啟動

```bash
uv run python -m echo.main
```

Bot 啟動時會載入功能並同步 Discord 應用程式指令。邀請 Bot 後，在頻道訊息中提及它即可開始對話。

## 指令

| 指令 | 權限 | 功能 |
| --- | --- | --- |
| `/memory delete` | 所有人 | 刪除自己的短期記憶、長期記憶與角色狀態 |
| `/memory delete_all` | Bot owner | 刪除所有使用者的短期記憶、長期記憶與角色狀態 |

## 自訂角色 Prompt

預設 Prompt 位於 `prompt/`，程式會載入以下檔案：

- `system/aemeath.md`
- `system/behavior.md`
- `system/safety.md`
- `context/relationship.md`
- `context/world.md`
- `context/character_state.md`

可直接修改這些 Markdown 檔案調整角色設定、互動方式、世界觀與狀態描述。若將 Prompt 放在其他目錄，請設定 `PROMPT_DIR`。

## 資料與隱私

對話會傳送至設定的 OpenAI-compatible API；短期記憶保存在 SQLite，長期記憶交由設定的 Mem0-compatible 服務保存，角色狀態保存在 JSON 檔案。使用網路搜尋時，搜尋請求會送至設定的 SearXNG 服務，網頁擷取則會直接連線至指定網站。請確認這些服務符合你的資料處理與隱私需求，並妥善保管 `.env` 中的 Token 與 API 金鑰。

## 授權

本專案採用 [MIT License](LICENSE)。
