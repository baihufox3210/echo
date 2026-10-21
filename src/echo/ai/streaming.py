import json
import re


class PartialFieldExtractor:
    """從串流中的不完整 JSON 取出指定字串欄位目前已生成的內容。"""

    def __init__(self, field: str):
        self._key = re.compile(rf'"{re.escape(field)}"\s*:\s*"')
        self.buffer = ""

    def reset(self) -> None:
        self.buffer = ""

    def feed(self, delta: str) -> str | None:
        self.buffer += delta
        return self.value()

    def value(self) -> str | None:
        match = self._key.search(self.buffer)
        if match is None:
            return None

        raw = self.buffer[match.end():]
        end = _find_string_end(raw)
        if end is not None:
            raw = raw[:end]
        else:
            # 去掉尚未完整的跳脫序列
            raw = re.sub(r"\\(u[0-9a-fA-F]{0,3})?$", "", raw)

        try:
            return json.loads(f'"{raw}"')
        except json.JSONDecodeError:
            return None


def _find_string_end(raw: str) -> int | None:
    escaped = False
    for index, char in enumerate(raw):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return index
    return None


class DialogueStream:
    """取得串流中目前可顯示的回覆文字。

    正常情況下從 JSON 的 dialogue 欄位取出；若模型直接輸出純文字（未遵守 JSON 格式），
    則直接顯示累積的文字。
    """

    PLAIN_TEXT_THRESHOLD = 8

    def __init__(self) -> None:
        self._extractor = PartialFieldExtractor("dialogue")

    @property
    def buffer(self) -> str:
        return self._extractor.buffer

    def reset(self) -> None:
        self._extractor.reset()

    def feed(self, delta: str) -> str | None:
        value = self._extractor.feed(delta)
        if value is not None:
            return value

        head = self.buffer.lstrip()
        if len(head) >= self.PLAIN_TEXT_THRESHOLD and not head.startswith(("{", "`", "[")):
            return head
        return None
