"""將使用者上傳的檔案或網路資源轉為純文字。"""

import html
import io
import logging
import re
import zipfile
from pathlib import PurePosixPath

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".jsonl",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env.example", ".xml", ".srt", ".vtt",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cs",
    ".go", ".rs", ".rb", ".php", ".swift", ".sh", ".bash", ".ps1", ".sql", ".lua", ".dart",
    ".css", ".scss", ".vue", ".svelte",
}
HTML_EXTENSIONS = {".html", ".htm", ".xhtml"}
TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIME_TYPES = {"application/json", "application/xml", "application/x-yaml", "application/javascript"}
HTML_MIME_TYPES = {"text/html", "application/xhtml+xml"}
PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

MAX_PDF_PAGES = 200


class UnsupportedDocument(Exception):
    pass


def kind_of(filename: str, content_type: str | None) -> str:
    """回傳 image / html / pdf / docx / text；不支援時拋出 UnsupportedDocument。"""
    mime = (content_type or "").split(";")[0].strip().lower()
    suffix = PurePosixPath(filename.lower()).suffix

    if mime in IMAGE_TYPES or suffix in IMAGE_EXTENSIONS:
        return "image"
    if mime in HTML_MIME_TYPES or suffix in HTML_EXTENSIONS:
        return "html"
    if mime == PDF_MIME or suffix == ".pdf":
        return "pdf"
    if mime == DOCX_MIME or suffix == ".docx":
        return "docx"
    if suffix in TEXT_EXTENSIONS or mime.startswith(TEXT_MIME_PREFIXES) or mime in TEXT_MIME_TYPES:
        return "text"
    raise UnsupportedDocument(f"不支援的檔案類型：{mime or suffix or '未知'}")


def extract_text(data: bytes, kind: str, encoding: str | None = None) -> tuple[str, str]:
    """回傳 (標題, 純文字)。在背景執行緒呼叫，避免阻塞事件迴圈。"""
    match kind:
        case "html":
            return extract_html(data, encoding)
        case "pdf":
            return "", extract_pdf(data)
        case "docx":
            return "", extract_docx(data)
        case "text":
            return "", decode_text(data, encoding)
    raise UnsupportedDocument(kind)


def decode_text(data: bytes, encoding: str | None = None) -> str:
    for candidate in filter(None, (encoding, "utf-8-sig", "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else None, "big5", "gb18030")):
        try:
            return data.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def extract_html(data: bytes, encoding: str | None = None) -> tuple[str, str]:
    soup = BeautifulSoup(data, "html.parser", from_encoding=encoding)

    title = soup.title.get_text(strip=True) if soup.title else ""
    description = ""
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    if meta and meta.get("content"):
        description = str(meta["content"]).strip()

    # 移除隱藏元素：常被用來藏匿提示詞注入
    for element in soup.select('[hidden], [aria-hidden="true"], [style*="display:none"], [style*="display: none"], [style*="visibility:hidden"]'):
        element.decompose()
    for element in soup(("script", "style", "noscript", "svg", "nav", "footer", "header",
                         "form", "aside", "iframe", "button", "template")):
        element.decompose()

    root = (
        soup.find("article")
        or soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.body
        or soup
    )

    text = dedupe_lines(root.get_text(separator="\n"))
    if description and description not in text:
        text = f"{description}\n\n{text}"
    return title, text


def extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        pages = []
        for index, page in enumerate(reader.pages[:MAX_PDF_PAGES], start=1):
            content = (page.extract_text() or "").strip()
            if content:
                pages.append(f"[第 {index} 頁]\n{content}")
    except Exception as error:
        raise UnsupportedDocument("PDF 無法解析") from error

    if not pages:
        raise UnsupportedDocument("PDF 沒有可擷取的文字（可能是掃描影像）")
    return "\n\n".join(pages)


def extract_docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError) as error:
        raise UnsupportedDocument("DOCX 無法解析") from error

    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return dedupe_lines(html.unescape(text))


def dedupe_lines(text: str) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        line = re.sub(r"[ \t　]+", " ", line).strip()
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines)


def paginate(text: str, start: int, size: int) -> tuple[str, int | None]:
    """取出 text[start:start+size]，回傳 (片段, 下一段起點或 None)。"""
    start = max(0, start)
    chunk = text[start:start + size]
    end = start + len(chunk)
    return chunk, (end if end < len(text) else None)
