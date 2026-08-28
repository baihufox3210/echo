# Echo

基於 Discord 的角色對話 Bot，整合 OpenAI-compatible API、短期記憶、Mem0 長期記憶與角色狀態保存。

## Features

- Discord mention-based conversation：被 Bot 提及時觸發角色對話。
- 結合 SQLite 短期記憶與 Mem0 長期記憶，維持跨訊息與跨對話的上下文。
- 以 JSON 保存每位使用者的角色狀態，例如好感度、心情、體力與信任度。
- Prompt 與程式邏輯分離，可獨立調整角色設定與互動規則。
- 使用結構化 AI 回應與分層錯誤處理，維持資料格式與服務穩定性。

## Requirements

- [Python](https://www.python.org/) >= 3.15
- [uv](https://docs.astral.sh/uv/)
- [Discord Bot](https://discord.com/developers/applications)
- OpenAI-compatible API
- Mem0-compatible memory service

## Setup

```bash
uv sync
```

在專案根目錄建立 `.env`：

```env
DISCORD_TOKEN=your_discord_token
BASE_URL=https://api.example.com/v1
API_KEY=your_api_key
MODEL=your_model
MEM0_BASE_URL=http://localhost:8888
```

可選設定：

```env
MEMORY_DATABASE_PATH=data/memory.db
CHARACTER_STATE_PATH=data/character_state.json
PROMPT_DIR=prompt
```

## Run

```bash
uv run python -m echo.main
```

## Commands

- `/memory delete`：刪除目前使用者的短期記憶、長期記憶與角色狀態
- `/memory delete_all`：Bot owner 刪除所有使用者資料

## Open Source

本專案採用 [MIT License](https://opensource.org/license/mit)，允許使用、修改與再發布，但須保留著作權聲明。完整授權條款請見根目錄的 [LICENSE](LICENSE) 檔案。