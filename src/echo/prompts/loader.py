from pathlib import Path

from echo.core.errors import PromptError

SYSTEM_PROMPT_FILES = (
    "system/aemeath.md",
    "system/behavior.md",
    "system/safety.md",
    "context/relationship.md",
    "context/world.md",
)
CHARACTER_STATE_PROMPT_FILE = "context/character_state.md"


class PromptLoader:
    """載入 Prompt 檔案並依修改時間快取，修改檔案後無需重啟即可生效。"""

    def __init__(self, prompt_dir: Path):
        self.prompt_dir = prompt_dir.resolve()
        self._cache: dict[Path, tuple[float, str]] = {}

    def load(self, name: str) -> str:
        path = (self.prompt_dir / name).resolve()

        if not path.is_relative_to(self.prompt_dir):
            raise PromptError(f"Invalid prompt path: {name}")

        try:
            mtime = path.stat().st_mtime
            cached = self._cache.get(path)
            if cached and cached[0] == mtime:
                return cached[1]

            text = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as error:
            raise PromptError(f"Prompt not found: {path}") from error
        except OSError as error:
            raise PromptError(f"Prompt could not be loaded: {path}") from error

        self._cache[path] = (mtime, text)
        return text

    def load_system_prompt(self) -> str:
        return "\n\n".join(self.load(name) for name in SYSTEM_PROMPT_FILES)

    def load_character_state_prompt(self) -> str:
        return self.load(CHARACTER_STATE_PROMPT_FILE)
