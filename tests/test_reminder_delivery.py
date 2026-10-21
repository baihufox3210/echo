from types import SimpleNamespace

import discord

from echo.cogs.maintenance import Maintenance


class FakeChannel:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send(self, content, allowed_mentions=None):  # noqa: D401
        if self.fail:
            raise discord.HTTPException(SimpleNamespace(status=403, reason="Forbidden"), "no")
        self.sent.append((content, allowed_mentions))


def make_cog(channel, user, ping=True):
    cog = object.__new__(Maintenance)
    cog.settings = SimpleNamespace(reminder_ping=ping)
    cog.bot = SimpleNamespace(get_channel=lambda _id: channel, get_user=lambda _id: user)
    return cog


async def test_reminder_pings_only_the_target_user():
    channel = FakeChannel()
    await make_cog(channel, None)._send_reminder(7, 1, "⏰ 喝水")
    content, mentions = channel.sent[0]
    assert content == "<@7> ⏰ 喝水"
    assert [u.id for u in mentions.users] == [7] and mentions.everyone is False and mentions.roles is False


async def test_reminder_without_ping_and_dm_fallback():
    channel = FakeChannel()
    await make_cog(channel, None, ping=False)._send_reminder(7, 1, "⏰ 喝水")
    assert channel.sent[0][0] == "⏰ 喝水" and channel.sent[0][1].users is False

    dm = FakeChannel()
    await make_cog(FakeChannel(fail=True), dm)._send_reminder(7, 1, "⏰ 喝水")
    assert dm.sent[0][0] == "⏰ 喝水"
