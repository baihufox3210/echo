from dataclasses import dataclass, field

from pydantic import BaseModel, Field


@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str
    images: list[str] = field(default_factory=list)

    def to_input(self) -> dict:
        if not self.images:
            return {"role": self.role, "content": self.content}
        return {
            "role": self.role,
            "content": [
                {"type": "input_text", "text": self.content},
                *({"type": "input_image", "image_url": url, "detail": "auto"} for url in self.images),
            ],
        }


class CharacterState(BaseModel):
    affection: int = Field(ge=0, le=100, description="好感度 0~100")
    mood: int = Field(ge=0, le=100, description="心情 0~100")
    stamina: int = Field(ge=0, le=100, description="體力 0~100")
    trust: int = Field(ge=0, le=100, description="信任度 0~100")


class AIResponse(BaseModel):
    dialogue: str = Field(
        description=(
            "要直接傳送給使用者的角色對話，依序包含：動作／神態描寫、『表面台詞』、（內心獨白）。"
            "不要在此放入好感度標記、思緒摘要、行動推薦、人物狀態板或來源網址——那些另有欄位。"
            "若需摘要長篇內容（如檔案、網頁），請精簡重點，不要逐字照抄。"
        )
    )
    thoughts: list[str] = Field(
        description="思緒摘要：她注意到什麼、最在意什麼、想做什麼、害怕什麼；無則給空陣列。"
    )
    suggestions: list[str] = Field(
        description="行動推薦：2～4 個可選行動，符合場景與好感度；不需要時給空陣列。"
    )
    state: CharacterState = Field(description="本次互動結束後的角色狀態絕對值")
    sources: list[str] = Field(description="本次回答實際引用的網址（最多 5 個，未使用網路資料則為空陣列）")


@dataclass(slots=True)
class TokenUsage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True)
class ChatResult:
    response: AIResponse
    previous_state: CharacterState
    sources: list[str]
    usage: TokenUsage
    tools_used: list[str] = field(default_factory=list)
    reactions: list[str] = field(default_factory=list)
