from dataclasses import dataclass
from pydantic import BaseModel, Field

@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str
    
class CharacterState(BaseModel):
    affection: int = Field(ge=0, le=100)
    mood: int = Field(ge=0, le=100)
    stamina: int = Field(ge=0, le=100)
    trust: int = Field(ge=0, le=100)
    
@dataclass(slots=True)
class AIResponse:
    dialogue: str
    action: str
    state: CharacterState