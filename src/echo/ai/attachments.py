"""處理使用者上傳的附件：圖片轉為 data URL 供視覺模型使用，文件轉為純文字。"""

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from echo.ai.documents import UnsupportedDocument, extract_text, kind_of
from echo.core.config import Settings

logger = logging.getLogger(__name__)

IMAGE_MAX_BYTES = 5_000_000


@dataclass(slots=True)
class IncomingFile:
    filename: str
    content_type: str | None
    size: int
    read: Callable[[], Awaitable[bytes]]


@dataclass(slots=True)
class Document:
    file_id: str
    filename: str
    text: str


@dataclass
class ProcessedFiles:
    images: list[str] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)


async def process_files(files: list[IncomingFile], settings: Settings) -> ProcessedFiles:
    processed = ProcessedFiles()

    for index, file in enumerate(files[: settings.max_attachments], start=1):
        processed.names.append(file.filename)
        try:
            kind = kind_of(file.filename, file.content_type)
        except UnsupportedDocument as error:
            processed.notes.append(f"{file.filename}：{error}")
            continue

        limit = IMAGE_MAX_BYTES if kind == "image" else settings.attachment_max_bytes
        if file.size > limit:
            processed.notes.append(f"{file.filename}：檔案過大（上限 {limit // 1_000_000} MB）")
            continue
        if kind == "image" and not settings.enable_vision:
            processed.notes.append(f"{file.filename}：目前未啟用圖片辨識")
            continue

        try:
            data = await file.read()
        except Exception:
            logger.warning("Failed to download attachment %s", file.filename, exc_info=True)
            processed.notes.append(f"{file.filename}：下載失敗")
            continue

        if kind == "image":
            mime = (file.content_type or "image/png").split(";")[0]
            processed.images.append(f"data:{mime};base64,{base64.b64encode(data).decode()}")
            logger.info("Attached image %s (%s, %d bytes)", file.filename, mime, len(data))
            continue

        try:
            _, text = await asyncio.to_thread(extract_text, data, kind)
        except UnsupportedDocument as error:
            processed.notes.append(f"{file.filename}：{error}")
            continue

        processed.documents.append(Document(f"file{index}", file.filename, text))

    if len(files) > settings.max_attachments:
        processed.notes.append(f"一次最多處理 {settings.max_attachments} 個附件，其餘已略過")

    for note in processed.notes:
        logger.warning("Attachment skipped: %s", note)

    return processed
