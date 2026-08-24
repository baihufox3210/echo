from pathlib import Path

class PromptLoader:
    def __init__(self, prompt_dir: Path):
        self.prompt_dir = prompt_dir

    def load(self, name: str) -> str:
        path = self.prompt_dir / name

        if not path.exists():
            raise FileNotFoundError(f"Prompt not found: {path}")

        return path.read_text(encoding="utf-8").strip()

    def load_system_prompt(self) -> str:
        parts = [
            self.load("system/aemeath.md"),
            self.load("system/behavior.md"),
            self.load("system/safety.md"),
            self.load("context/relationship.md"),
            self.load("context/world.md"),
        ]

        return "\n\n".join(parts)
    
    def load_character_state_prompt(self) -> str:
        return self.load("context/character_state.md")