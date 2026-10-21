from echo.core.text import split_message


def test_short_message_is_single_chunk():
    assert split_message("hello") == ["hello"]


def test_long_message_splits_on_newline_within_limit():
    text = "\n".join(["a" * 30] * 100)
    chunks = split_message(text, limit=200)
    assert all(len(c) <= 200 for c in chunks)
    assert "\n".join(chunks) == text


def test_hard_cut_without_separator():
    chunks = split_message("x" * 4500)
    assert [len(c) for c in chunks] == [2000, 2000, 500]
