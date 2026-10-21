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
    dialogue: str = Field(description="要直接傳送給使用者的完整回覆內容，不要在此列出來源網址")
    action: str = Field(description="角色本輪動作或神態的簡短摘要")
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
