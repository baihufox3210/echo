from types import SimpleNamespace

import discord

from echo.cogs.chat import Chat

BOT_ID, USER_A, USER_B = 1, 100, 200


class FakeMessage(SimpleNamespace):
    pass


def message(author_id, content, reference=None, mid=0, name="使用者"):
    return FakeMessage(
        id=mid,
        author=SimpleNamespace(id=author_id, display_name=name),
        clean_content=content,
        attachments=[],
        reference=reference,
    )


def make_cog(cached=()):
    cog = object.__new__(Chat)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=BOT_ID), cached_messages=list(cached))
    return cog


def ref(resolved, message_id=None):
    return SimpleNamespace(resolved=resolved, message_id=message_id or getattr(resolved, "id", None))


def test_quoted_marks_other_users_message(monkeypatch):
    monkeypatch.setattr(discord, "Message", FakeMessage)
    other = message(USER_B, "我喜歡拉麵", mid=5, name="小明")
    incoming = message(USER_A, "你看他說的", reference=ref(other))
    assert make_cog()._quoted(incoming) == "其他使用者「小明」的訊息：我喜歡拉麵"


def test_quoted_marks_bot_reply_to_someone_else(monkeypatch):
    monkeypatch.setattr(discord, "Message", FakeMessage)
    b_message = message(USER_B, "晚安", mid=10)
    bot_reply = message(BOT_ID, "晚安喔～", reference=SimpleNamespace(resolved=None, message_id=10), mid=11)
    incoming = message(USER_A, "那我呢", reference=ref(bot_reply))

    quoted = make_cog(cached=[b_message])._quoted(incoming)
    assert quoted.startswith("你先前回覆「其他使用者」的訊息")


def test_quoted_marks_bot_reply_to_same_user(monkeypatch):
    monkeypatch.setattr(discord, "Message", FakeMessage)
    a_message = message(USER_A, "早安", mid=20)
    bot_reply = message(BOT_ID, "早安～", reference=SimpleNamespace(resolved=None, message_id=20), mid=21)
    incoming = message(USER_A, "嗯", reference=ref(bot_reply))

    assert make_cog(cached=[a_message])._quoted(incoming).startswith("你先前傳給這位使用者的訊息")
