# Echo

Echo 是一個以 Discord 為介面的 AI 角色對話 Bot。使用者提及 Bot、回覆 Bot 的訊息或私訊 Bot 即可開始聊天；Echo 會結合可自訂的角色 Prompt、短期對話記錄、長期記憶與角色狀態，延續個人化互動。AI 也可查詢目前時間、透過 SearXNG 搜尋網路，以及讀取網頁內容。

## 功能

- **Discord 對話**：提及 Bot、回覆 Bot 的訊息或私訊皆可觸發；Bot 以「回覆」形式回應，但不會 ping 任何人（包括 @everyone 與身分組）。
- **多人同時處理**：不同使用者的請求並行處理，同一使用者的請求依序處理，避免對話紀錄與角色狀態互相覆蓋；可設定 AI 最大並行數。
- **流量限制**：每位使用者預設每分鐘最多 3 次請求。
- **長訊息分段**：超過 Discord 2000 字上限的回覆會自動在段落處分段傳送。
- **短期記憶**：以 SQLite（WAL 模式）保存每位使用者最近 20 則訊息。
- **長期記憶**：透過 Mem0-compatible API 搜尋並保存對話記憶；寫入在背景執行，不拖慢回覆。服務失效時會自動降級，不影響聊天。
- **角色狀態**：以 SQLite 保存每位使用者的好感度、心情、體力與信任度（舊版 JSON 會自動匯入）。
- **可編輯 Prompt**：角色、互動規則、世界觀與狀態描述皆以 Markdown 管理，修改後無需重啟即生效。
- **連網搜尋**：
  - 支援時間範圍（日／週／月／年）與類別（一般、新聞、科技、學術）。
  - 結果去重、附發布日期，並包含 SearXNG 的即時答案與資訊框。
  - 搜尋與網頁擷取結果快取 5 分鐘，連線失敗自動重試。
  - 網頁擷取優先取出 `<article>`／`<main>` 正文，限制下載大小，並阻擋存取內網位址（SSRF 防護）。
  - 回答時會附上引用來源連結。
- **記憶管理**：使用者可查看狀態、刪除自己的資料；Bot owner 可刪除所有使用者資料。

## 使用 Docker 部署（建議）

需求：[Docker](https://docs.docker.com/get-docker/) 與 Docker Compose。

1. 在 Discord Developer Portal 建立 Bot，取得 Bot Token，並在 Bot 設定中啟用 **Message Content Intent**。邀請 Bot 時選取 `bot` 與 `applications.commands` scopes，並授予讀取、傳送訊息與讀取訊息歷史的權限。

2. 建立設定檔並填入必要值：

   ```bash
   cp .env.example .env
   # 產生 SearXNG 金鑰
   echo "SEARXNG_SECRET=$(openssl rand -hex 32)" >> .env
   ```

3. 啟動：

   ```bash
   docker compose up -d --build
   docker compose logs -f echo
   ```

Compose 會同時啟動 Echo 與內建的 SearXNG（設定位於 `searxng/settings.yml`，已啟用 JSON 格式）。資料保存在 `echo-data` volume，`prompt/` 以唯讀方式掛載，修改 Prompt 後無需重建映像。

常用操作：

```bash
docker compose pull searxng && docker compose up -d --build   # 更新
docker compose down                                            # 停止
```

若要沿用舊版本機執行時的資料：

```bash
docker compose cp data/. echo:/app/data/
docker compose restart echo
```

> 若 Mem0 服務跑在主機上，請在 `.env` 設定 `MEM0_BASE_URL=http://host.docker.internal:8888`。

## 本機執行

需求：[Python](https://www.python.org/) 3.15 以上與 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync
cp .env.example .env   # 填入設定
uv run echo-bot
```

本機執行時若需要網路搜尋，請自行啟動 SearXNG 並設定 `SEARXNG_URL`（預設 `http://127.0.0.1:8080`）。

執行測試：

```bash
uv run pytest
```

## 設定

必要設定：

| 變數 | 說明 |
| --- | --- |
| `DISCORD_TOKEN` | Discord Bot Token |
| `BASE_URL` | OpenAI-compatible API 的 Base URL（模型需支援 tool calling 與 JSON schema 結構化輸出） |
| `API_KEY` | AI API 金鑰 |
| `MODEL` | 要使用的模型名稱 |
| `SEARXNG_SECRET` | 僅 Docker Compose 需要，SearXNG 的金鑰 |

選用設定（括號中為預設值）：

| 變數 | 說明 |
| --- | --- |
| `MEM0_BASE_URL`（空） | Mem0-compatible 記憶服務的 Base URL，需支援 `/memories` 與 `/search`；留空則停用長期記憶 |
| `MEM0_TOP_K` (`10`) | 每次檢索的長期記憶數量 |
| `MEM0_TIMEOUT` (`60`) | 記憶服務逾時秒數 |
| `MODEL_TEMPERATURE`（空） | 模型 temperature，留空使用模型預設 |
| `AI_TIMEOUT` (`90`) | AI API 逾時秒數 |
| `AI_MAX_RETRIES` (`2`) | AI API 失敗重試次數 |
| `AI_MAX_TURNS` (`8`) | 單次對話中 AI 最多可使用工具的回合數 |
| `MAX_CONCURRENT_REQUESTS` (`8`) | 同時送往 AI 的最大請求數，超過的請求會排隊 |
| `RATE_LIMIT_PER_USER` (`3`) | 每位使用者在時間視窗內的請求上限 |
| `RATE_LIMIT_WINDOW` (`60`) | 流量限制時間視窗（秒） |
| `MAX_MESSAGE_LENGTH` (`4000`) | 使用者訊息長度上限 |
| `RESPOND_TO_DM` (`true`) | 是否回應私訊 |
| `RESPOND_TO_REPLIES` (`true`) | 回覆 Bot 的訊息時是否視為對話 |
| `SYNC_COMMANDS` (`true`) | 啟動時是否同步斜線指令 |
| `MEMORY_DATABASE_PATH` (`data/memory.db`) | SQLite 資料庫路徑（短期記憶與角色狀態） |
| `CHARACTER_STATE_PATH` (`data/character_state.json`) | 舊版角色狀態 JSON，存在時會自動匯入資料庫 |
| `SHORT_TERM_MAX_MESSAGES` (`20`) | 每位使用者保留的短期記憶訊息數 |
| `PROMPT_DIR` (`prompt`) | Prompt 檔案目錄 |
| `SEARXNG_URL` (`http://127.0.0.1:8080`) | SearXNG 服務網址；留空則停用搜尋（仍可讀取網頁） |
| `SEARCH_TIMEOUT` (`20`) | 搜尋與網頁擷取逾時秒數 |
| `SEARCH_MAX_RESULTS` (`5`) | 每次搜尋最多回傳的結果數 |
| `SEARCH_LANGUAGE` (`zh-TW`) | 搜尋語言 |
| `SEARCH_CACHE_TTL` (`300`) | 搜尋快取秒數，設為 `0` 停用 |
| `FETCH_MAX_CHARS` (`12000`) | 網頁擷取回傳給 AI 的最大字數 |
| `FETCH_MAX_BYTES` (`3000000`) | 網頁擷取最大下載位元組數 |
| `FETCH_ALLOW_PRIVATE` (`false`) | 是否允許擷取內網位址 |
| `TIMEZONE` (`Asia/Taipei`) | 時間工具與 Prompt 使用的時區 |
| `LOG_LEVEL` (`INFO`) | 日誌等級 |

## 指令

| 指令 | 權限 | 功能 |
| --- | --- | --- |
| `/memory status` | 所有人 | 查看角色目前對自己的狀態與短期記憶數量 |
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

可直接修改這些 Markdown 檔案調整角色設定、互動方式、世界觀與狀態描述，修改後下一則訊息即生效。`context/character_state.md` 中可使用 `{affection}`、`{mood}`、`{stamina}`、`{trust}` 佔位符；程式也會自動附上目前狀態數值。若將 Prompt 放在其他目錄，請設定 `PROMPT_DIR`。

## 資料與隱私

對話會傳送至設定的 OpenAI-compatible API；短期記憶與角色狀態保存在 SQLite，長期記憶交由設定的 Mem0-compatible 服務保存。使用網路搜尋時，搜尋請求會送至設定的 SearXNG 服務，網頁擷取則會直接連線至指定網站。請確認這些服務符合你的資料處理與隱私需求，並妥善保管 `.env` 中的 Token 與 API 金鑰。

## 授權

本專案採用 [MIT License](LICENSE)。
