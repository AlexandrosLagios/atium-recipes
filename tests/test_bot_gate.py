import pytest
from telegram.ext import ApplicationHandlerStop

from recipebot.bot import make_gate


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeUpdate:
    def __init__(self, user_id):
        self.effective_user = FakeUser(user_id) if user_id is not None else None


async def test_the_allowed_user_passes_the_gate():
    gate = make_gate(frozenset({12345}))

    await gate(FakeUpdate(12345), None)


async def test_a_foreign_user_is_stopped():
    gate = make_gate(frozenset({12345}))

    with pytest.raises(ApplicationHandlerStop):
        await gate(FakeUpdate(999), None)


async def test_an_update_with_no_user_is_stopped():
    gate = make_gate(frozenset({12345}))

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
    gate = make_gate(frozenset({12345}))

    with pytest.raises(ApplicationHandlerStop):
        await gate(RaisingUpdate(), None)
