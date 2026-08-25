import asyncio
from pathlib import Path

from echo.ai.client import AIClient
from echo.ai.service import AIService
from echo.core.config import Settings
from echo.memory.short_term import ShortTermMemory
from echo.prompts.loader import PromptLoader
from echo.storage.character_state import CharacterStateStore


async def main():
    # =========================
    # 基本設定
    # =========================

    settings = Settings()

    prompt_dir = Path("prompt")
    state_path = Path("data/character_state.json")

    # =========================
    # 建立 dependencies
    # =========================

    client = AIClient(settings)

    prompt_loader = PromptLoader(
        prompt_dir=prompt_dir,
    )

    memory = ShortTermMemory(
        database_path="data/memory.db",
    )

    # 初始化 SQLite
    await memory.initialize()

    state_store = CharacterStateStore(
        path=state_path,
    )

    service = AIService(
        client=client,
        prompt_loader=prompt_loader,
        memory=memory,
        state_store=state_store,
    )

    # =========================
    # 測試 user
    # =========================

    user_id = 999999999

    user_message = "我今天特別準備了一份禮物送給你。"

    print("================================")
    print("開始測試 AIService")
    print("================================")

    print(f"User ID : {user_id}")
    print(f"Message : {user_message}")

    # =========================
    # 呼叫 Service
    # =========================

    result = await service.chat(
        user_id=user_id,
        user_message=user_message,
    )

    # =========================
    # 輸出 AI 回應
    # =========================

    print("\n================================")
    print("AI Response")
    print("================================")

    print(f"Dialogue : {result.dialogue}")
    print(f"Action   : {result.action}")

    # =========================
    # 輸出 Character State
    # =========================

    print("\n================================")
    print("Character State")
    print("================================")

    print(f"Affection : {result.state.affection}")
    print(f"Mood      : {result.state.mood}")
    print(f"Stamina   : {result.state.stamina}")
    print(f"Trust     : {result.state.trust}")

    # =========================
    # 驗證數值
    # =========================

    assert 0 <= result.state.affection <= 100
    assert 0 <= result.state.mood <= 100
    assert 0 <= result.state.stamina <= 100
    assert 0 <= result.state.trust <= 100

    print("\n================================")
    print("Structured Output 驗證成功")
    print("================================")

    # =========================
    # 驗證 JSON
    # =========================

    if state_path.exists():
        print(f"\nState file: {state_path}")
        print("Character State JSON 已成功更新。")
    else:
        raise RuntimeError(
            "Character State JSON 不存在，可能 state_store 沒有成功儲存。"
        )


if __name__ == "__main__":
    asyncio.run(main())