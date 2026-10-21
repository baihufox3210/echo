# Echo

Echo 是一個以 Discord 為介面的 AI 角色對話 Bot。使用者提及 Bot、回覆 Bot 的訊息或私訊 Bot 即可開始聊天；Echo 會結合可自訂的角色 Prompt、短期對話記錄、長期記憶與角色狀態，延續個人化互動。AI 也可查詢目前時間、透過 SearXNG 搜尋網路，以及讀取網頁內容。

## 功能

- **Discord 對話**：提及 Bot、回覆 Bot 的訊息或私訊皆可觸發；Bot 以「回覆」形式回應，但不會 ping 任何人（包括 @everyone 與身分組）。
- **即時串流回覆**：代理執行時即時顯示進度（🔍 搜尋中、📄 閱讀網頁、🔬 深入研究…），回覆文字邊生成邊更新到同一則訊息；更新會合併送出，避免觸發 Discord 頻率限制。
- **Embed 資訊卡**：回覆下方以 Embed 呈現角色狀態（進度條與本輪變化 ▲▼）、參考來源連結、使用的工具與 token 用量。
- **多人同時處理**：不同使用者並行處理，同一使用者依序處理，避免對話紀錄與角色狀態互相覆蓋；可設定 AI 最大並行數。
- **附件讀取**：
  - 圖片：交給支援視覺的模型辨識（模型不支援時自動改為不帶圖片重試）。
  - 文件：純文字、Markdown、程式碼、JSON、CSV、HTML、PDF、DOCX；長檔案只附上開頭，AI 可用 `read_attachment` 分段讀取其餘內容。
- **多步驟 AI 代理**：
  - 一次回覆中可多次、連續或並行呼叫工具，直到資訊足夠再作答。
  - `deep_research` 研究子代理：自行規劃、多輪搜尋、閱讀多個來源並交叉比對，回傳附來源的研究摘要。
  - 回合數用盡時，自動以「禁止呼叫工具」模式根據已取得的資訊整理作答，而不是直接失敗。
  - 連線錯誤、輸出格式錯誤等可重試的失敗會自動重新執行（`AI_MAX_RUNS`）。
  - 共 20 種工具，見下方「AI 工具一覽」；可用 `DISABLED_TOOLS` 停用不需要的工具。
- **進階連網搜尋**：
  - 時間範圍、類別（一般／新聞／科技／學術／影片）、語言、頁數。
  - 進階條件：限定網站、排除網站、完整詞組、排除詞、檔案類型（同時在本地再過濾一次，避免引擎不支援 `site:`）。
  - `multi_search` 並行查詢多組關鍵字，以 Reciprocal Rank Fusion 合併排序。
  - 結果去重、附發布日期，包含 SearXNG 即時答案與資訊框；查無結果時提供建議與更正。
  - 網頁擷取支援 HTML／PDF／DOCX／純文字，可分段閱讀長文件；限制下載大小並阻擋內網位址（SSRF 防護）。
  - 搜尋與擷取結果快取 5 分鐘，連線失敗自動重試。
- **提示詞注入防禦**：
  - 網頁、搜尋結果、附件、長期記憶一律以每次請求隨機產生的邊界標記包裹，並在系統提示中明確規定其內容只是資料。
  - 清除可偽造邊界或對話角色的特殊符號、零寬字元，並移除網頁中隱藏的元素。
  - 偵測常見的中英文注入語句，加上警示並記錄日誌。
  - 系統提示內含隨機 canary；回覆若包含 canary 或大量設定原文，會自動重新產生，仍失敗則以安全回覆取代。串流中偵測到洩漏時也會立即停止顯示。
  - 回覆中的來源連結只保留工具實際取得過的網址，避免被注入釣魚連結。
  - 每次對話設有工具使用預算，避免被惡意內容誘導無限呼叫工具。
- **記憶與狀態**：
  - 短期記憶：以 SQLite（WAL 模式）保存每位使用者最近 20 則訊息（附件只記錄檔名）。
  - 長期記憶：透過 Mem0-compatible API 搜尋與保存，寫入在背景執行；服務失效時自動降級。
  - 角色狀態：以 SQLite 保存好感度、心情、體力與信任度（舊版 JSON 會自動匯入）。
  - 自然衰減：超過設定天數未互動，好感度每天自動下降；AI 也會得知距離上次互動多久。
- **提醒**：對 Echo 說「明天早上八點提醒我…」即可設定，時間到時在原頻道提醒（只會 ping 本人），也可用 `/reminder` 管理。
- **使用者筆記**：AI 會主動記下對方的稱呼、生日、喜好等重點，之後每次對話都記得；可在 `/memory status` 查看。
- **群組情境理解**：使用者回覆其他人的訊息時會一併提供被回覆的內容；AI 也能在需要時讀取頻道最近的對話。
- **表情反應**：角色可以對使用者的訊息加上表情符號表達情緒。
- **用量統計**：記錄每位使用者每日的 AI 請求與 token 用量，可用 `/usage` 查看。
- **可編輯 Prompt**：角色、互動規則、世界觀與狀態描述皆以 Markdown 管理，修改後無需重啟即生效。
- **Docker 部署**：內建 SearXNG、健康檢查、非 root 執行、資料持久化與優雅關閉。

## 使用 Docker 部署（建議）

需求：[Docker](https://docs.docker.com/get-docker/) 與 Docker Compose。

1. 在 Discord Developer Portal 建立 Bot，取得 Bot Token，並在 Bot 設定中啟用 **Message Content Intent**。邀請 Bot 時選取 `bot` 與 `applications.commands` scopes，並授予讀取訊息、傳送訊息、讀取訊息歷史與新增反應的權限。

2. 建立設定檔：

   ```bash
   ./setup.sh
   ```

   腳本會從 `.env.example` 建立 `.env`，並自動產生 SearXNG 金鑰（不需要 openssl）。接著編輯 `.env` 填入 `DISCORD_TOKEN`、`BASE_URL`、`API_KEY`、`MODEL`，再執行一次 `./setup.sh` 確認設定完整。

3. 啟動：

   ```bash
   docker compose up -d --build
   docker compose logs -f echo
   ```

Compose 會同時啟動 Echo 與內建的 SearXNG（設定位於 `searxng/settings.yml`，已啟用 JSON 格式）。資料保存在 `echo-data` volume，`prompt/` 以唯讀方式掛載，修改 Prompt 後無需重建映像。容器內建健康檢查：Bot 與 Discord 連線正常時會定期更新心跳，可用 `docker ps` 查看 `healthy` 狀態。

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

## AI 工具一覽

| 類別 | 工具 | 說明 |
| --- | --- | --- |
| 時間 | `get_current_time`、`convert_time` | 查詢任一時區的時間；跨時區換算（支援「紐約」、「東京」等中文地名） |
| 計算 | `calculate`、`convert_currency` | 安全的數學計算；以最新匯率換算貨幣（ExchangeRate-API，免金鑰） |
| 遊戲 | `roll_dice`、`random_pick` | 擲骰（如 `2d20+3`）；公平抽選 |
| 搜尋 | `web_search`、`multi_search`、`web_fetch`、`deep_research` | 進階網路搜尋、多查詢並行、閱讀網頁／PDF、研究子代理 |
| 知識 | `wikipedia`、`get_weather` | 維基百科條目摘要（中文優先顯示臺灣正體）；天氣與 7 天預報（Open-Meteo，免金鑰） |
| 記憶 | `remember_note`、`forget_note`、`recall_memory` | 記下／刪除使用者重點；查詢長期記憶 |
| 提醒 | `set_reminder`、`list_reminders`、`cancel_reminder` | 設定、查看、取消提醒 |
| 情境 | `read_channel_history`、`add_reaction`、`read_attachment` | 讀取頻道近期對話；表情反應；分段讀取附件 |

所有來自外部的工具結果（網頁、維基百科、頻道對話、附件、筆記與記憶）都會經過提示詞注入防禦處理。

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
| `AI_MAX_TURNS` (`10`) | 單次對話中 AI 最多可使用工具的回合數，用盡時會整理已有資訊作答 |
| `AI_MAX_RUNS` (`2`) | 發生可重試錯誤時，整個代理最多執行幾次 |
| `TOOL_CALL_BUDGET` (`24`) | 單次對話（含研究子代理）最多可呼叫的工具次數 |
| `ENABLE_RESEARCH` (`true`) | 是否啟用 `deep_research` 研究子代理 |
| `RESEARCH_MAX_TURNS` (`12`) | 研究子代理最多可使用的回合數 |
| `DISABLED_TOOLS`（空） | 以逗號分隔要停用的工具名稱，例如 `get_weather,read_channel_history` |
| `REMINDER_PING` (`true`) | 提醒送達時是否 ping 設定提醒的本人（不會 ping 其他人） |
| `STREAM_RESPONSES` (`true`) | 是否串流顯示回覆文字（模型需支援串流） |
| `STREAM_EDIT_INTERVAL` (`1.2`) | 串流時編輯 Discord 訊息的最短間隔秒數 |
| `SHOW_STATE_EMBED` (`true`) | 是否在回覆下方顯示角色狀態資訊卡 |
| `ENABLE_VISION` (`true`) | 是否將圖片附件交給模型辨識 |
| `MAX_ATTACHMENTS` (`4`) | 每則訊息最多處理的附件數 |
| `ATTACHMENT_MAX_BYTES` (`8000000`) | 文件附件大小上限（圖片上限 5 MB） |
| `ATTACHMENT_INLINE_CHARS` (`6000`) | 文件直接附在訊息中的字數，其餘由 AI 分段讀取 |
| `STATE_DECAY_ENABLED` (`true`) | 是否啟用好感度自然衰減 |
| `STATE_DECAY_AFTER_DAYS` (`3`) | 幾天未互動後開始衰減 |
| `STATE_DECAY_AMOUNT` (`1`) | 每天衰減的好感度 |
| `HEALTHCHECK_FILE` (`/tmp/echo-heartbeat`) | 健康檢查心跳檔路徑 |
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
| `/memory status` | 所有人 | 以 Embed 查看角色狀態、記住的事、提醒數量、短期記憶與上次互動時間 |
| `/memory delete` | 所有人 | 刪除自己的記憶、筆記、提醒、角色狀態與用量紀錄 |
| `/memory delete_all` | Bot owner | 刪除所有使用者的短期記憶、長期記憶與角色狀態 |
| `/reminder list` | 所有人 | 查看自己的提醒 |
| `/reminder cancel` | 所有人 | 取消提醒 |
| `/usage` | 所有人 | 查看自己今日、近 7 天、近 30 天與累計的 AI 用量 |
| `/usage scope:全部` | Bot owner | 查看全體用量與近 30 天用量排行 |

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

對話（包含上傳的圖片與文件內容）會傳送至設定的 OpenAI-compatible API；短期記憶與角色狀態保存在 SQLite，長期記憶交由設定的 Mem0-compatible 服務保存。使用網路搜尋時，搜尋請求會送至設定的 SearXNG 服務，網頁擷取則會直接連線至指定網站；天氣、維基百科與匯率工具會分別連線至 Open-Meteo、Wikipedia 與 ExchangeRate-API。AI 讀取頻道對話時，頻道中其他人的訊息也會傳送至 AI API，若不希望如此，請以 `DISABLED_TOOLS=read_channel_history` 停用。請確認這些服務符合你的資料處理與隱私需求，並妥善保管 `.env` 中的 Token 與 API 金鑰。

## 授權

本專案採用 [MIT License](LICENSE)。
