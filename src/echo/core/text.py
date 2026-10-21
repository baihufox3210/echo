DISCORD_MESSAGE_LIMIT = 2000


def split_message(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """將過長文字切成多段，優先在段落、換行、空白處切割。"""
    text = text.strip()
    chunks: list[str] = []

    while len(text) > limit:
        window = text[:limit]
        cut = -1
        for separator in ("\n\n", "\n", "。", " "):
            cut = window.rfind(separator)
            if cut >= limit // 2:
                cut += len(separator)
                break
        else:
            cut = limit

        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()

    if text:
        chunks.append(text)
    return chunks
