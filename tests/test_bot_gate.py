import pytest
from telegram.ext import ApplicationHandlerStop

from recipebot.bot import make_gate


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeUpdate:
    def __init__(self, user_id):
        self.effective_user = FakeUser(user_id) if user_id is not None else None


class FakeAllowlist:
    def __init__(self, ids=(), raises=False):
        self.ids = frozenset(ids)
        self.raises = raises
        self.reads = 0

    def allowed_ids(self):
        self.reads += 1
        if self.raises:
            raise RuntimeError("database is gone")
        return self.ids


async def test_the_allowed_user_passes_the_gate():
    gate = make_gate(frozenset({12345}), FakeAllowlist())

    await gate(FakeUpdate(12345), None)


async def test_a_foreign_user_is_stopped():
    gate = make_gate(frozenset({12345}), FakeAllowlist())

    with pytest.raises(ApplicationHandlerStop):
        await gate(FakeUpdate(999), None)


async def test_an_update_with_no_user_is_stopped():
    gate = make_gate(frozenset({12345}), FakeAllowlist())

    with pytest.raises(ApplicationHandlerStop):
        await gate(FakeUpdate(None), None)


class RaisingUser:
    @property
    def id(self):
        raise RuntimeError("boom")


class RaisingUpdate:
    def __init__(self):
        self.effective_user = RaisingUser()


async def test_a_user_object_that_raises_on_id_access_still_stops_the_update():
    gate = make_gate(frozenset({12345}), FakeAllowlist())

    with pytest.raises(ApplicationHandlerStop):
        await gate(RaisingUpdate(), None)


async def test_a_user_allowed_only_in_the_database_passes_the_gate():
    gate = make_gate(frozenset({12345}), FakeAllowlist({999}))

    await gate(FakeUpdate(999), None)


async def test_an_environment_user_never_reads_the_database():
    allowlist = FakeAllowlist()
    gate = make_gate(frozenset({12345}), allowlist)

    await gate(FakeUpdate(12345), None)

    assert allowlist.reads == 0


async def test_an_unreadable_database_still_admits_the_environment_user():
    gate = make_gate(frozenset({12345}), FakeAllowlist(raises=True))

    await gate(FakeUpdate(12345), None)


async def test_an_unreadable_database_stops_everyone_else():
    gate = make_gate(frozenset({12345}), FakeAllowlist(raises=True))

    with pytest.raises(ApplicationHandlerStop):
        await gate(FakeUpdate(999), None)
