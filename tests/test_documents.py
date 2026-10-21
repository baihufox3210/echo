import io
import zipfile

import pytest

from echo.ai.documents import UnsupportedDocument, decode_text, extract_text, kind_of, paginate
from echo.ai.tools.calculator import CalculationError, evaluate
from echo.ai.streaming import PartialFieldExtractor


@pytest.mark.parametrize(
    ("name", "mime", "kind"),
    [
        ("a.png", "image/png", "image"),
        ("a.py", None, "text"),
        ("report.pdf", "application/octet-stream", "pdf"),
        ("doc.docx", None, "docx"),
        ("page.html", None, "html"),
        ("notes", "text/plain; charset=utf-8", "text"),
    ],
)
def test_kind_of(name, mime, kind):
    assert kind_of(name, mime) == kind


def test_kind_of_rejects_binaries():
    with pytest.raises(UnsupportedDocument):
        kind_of("a.exe", "application/octet-stream")


def test_decode_text_handles_big5():
    assert decode_text("繁體中文".encode("big5")) == "繁體中文"


def test_extract_docx():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            "<w:document><w:body><w:p><w:r><w:t>第一段 &amp; 內容</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>第二段</w:t></w:r></w:p></w:body></w:document>",
        )
    _, text = extract_text(buffer.getvalue(), "docx")
    assert text == "第一段 & 內容\n第二段"


def test_paginate():
    assert paginate("abcdef", 0, 4) == ("abcd", 4)
    assert paginate("abcdef", 4, 4) == ("ef", None)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [("1 + 2 * 3", 7), ("2^10", 1024), ("sqrt(16) + abs(-2)", 6.0), ("(1200 * 0.85) / 3", 340.0)],
)
def test_calculator(expression, expected):
    assert evaluate(expression) == expected


@pytest.mark.parametrize("expression", ["__import__('os')", "2 ** 100000", "open('x')", "a + 1"])
def test_calculator_rejects_unsafe(expression):
    with pytest.raises(CalculationError):
        evaluate(expression)


def test_partial_field_extractor_streams_dialogue():
    extractor = PartialFieldExtractor("dialogue")
    assert extractor.feed('{"dia') is None
    assert extractor.feed('logue": "你好\\') == "你好"
    assert extractor.feed('n世界\\u00') == "你好\n世界"
    assert extractor.feed('41", "action": "x"}') == "你好\n世界A"
