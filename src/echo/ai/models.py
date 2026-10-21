from dataclasses import dataclass

from pydantic import BaseModel, Field


@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str


class CharacterState(BaseModel):
    affection: int = Field(ge=0, le=100, description="好感度 0~100")
    mood: int = Field(ge=0, le=100, description="心情 0~100")
    stamina: int = Field(ge=0, le=100, description="體力 0~100")
    trust: int = Field(ge=0, le=100, description="信任度 0~100")


class AIResponse(BaseModel):
    dialogue: str = Field(description="要直接傳送給使用者的完整回覆內容")
    action: str = Field(description="角色本輪動作或神態的簡短摘要")
    state: CharacterState = Field(description="本次互動結束後的角色狀態絕對值")
