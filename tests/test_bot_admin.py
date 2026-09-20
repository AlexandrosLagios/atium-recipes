from types import SimpleNamespace

from recipebot import bot
from recipebot.config import Config

OWNER = 111
GUEST = 222
NEWCOMER = 6529645381


def a_config(**overrides) -> Config:
    fields = dict(
        telegram_token="123:abc",
        allowed_user_ids=frozenset({OWNER, GUEST}),
        owner_id=OWNER,
        notion_client_id="c",
        notion_client_secret="s",
        notion_redirect_uri="https://bot.example/oauth/callback",
        oauth_callback_port=8080,
        db_path=":memory:",
        llm_provider="gemini",
        llm_api_key="g-key",
    )
    return Config(**{**fields, **overrides})


class FakeAllowlist:
    def __init__(self, ids=()):
        self.ids = set(ids)

    def allowed_ids(self):
        return frozenset(self.ids)

    def allow(self, telegram_user_id):
        self.ids.add(telegram_user_id)

    def deny(self, telegram_user_id):
        self.ids.discard(telegram_user_id)


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


def an_update(user_id):
    message = FakeMessage()
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=message,
        effective_message=message,
    )


def a_context(allowlist, args, cfg=None):
    return SimpleNamespace(
        bot_data={"cfg": cfg or a_config(), "users": allowlist},
        args=args,
    )


async def run(handler, user_id, args, allowlist=None, cfg=None):
    allowlist = allowlist if allowlist is not None else FakeAllowlist()
    update = an_update(user_id)
    await handler(update, a_context(allowlist, args, cfg))
    return update.message.replies[-1], allowlist


async def test_the_owner_allows_a_new_id():
    reply, allowlist = await run(bot.on_allow, OWNER, [str(NEWCOMER)])

    assert allowlist.allowed_ids() == frozenset({NEWCOMER})
    assert str(NEWCOMER) in reply


async def test_an_allowed_guest_cannot_allow_anyone():
    reply, allowlist = await run(bot.on_allow, GUEST, [str(NEWCOMER)])

    assert allowlist.allowed_ids() == frozenset()
    assert "owner" in reply.lower()


async def test_an_id_that_is_not_a_number_is_refused():
    reply, allowlist = await run(bot.on_allow, OWNER, ["6529645381; rm -rf /"])

    assert allowlist.allowed_ids() == frozenset()
    assert "numeric" in reply.lower()


async def test_allow_with_no_argument_is_refused():
    reply, allowlist = await run(bot.on_allow, OWNER, [])

    assert allowlist.allowed_ids() == frozenset()
    assert "numeric" in reply.lower()


async def test_allowing_an_id_that_is_already_allowed_says_so():
    reply, allowlist = await run(
        bot.on_allow, OWNER, [str(NEWCOMER)], FakeAllowlist({NEWCOMER})
    )

    assert allowlist.allowed_ids() == frozenset({NEWCOMER})
    assert "already" in reply.lower()


async def test_allowing_an_id_that_the_environment_already_lists_says_so():
    reply, allowlist = await run(bot.on_allow, OWNER, [str(GUEST)])

    assert allowlist.allowed_ids() == frozenset()
    assert "already" in reply.lower()


async def test_the_owner_denies_an_id_they_added():
    reply, allowlist = await run(
        bot.on_deny, OWNER, [str(NEWCOMER)], FakeAllowlist({NEWCOMER})
    )

    assert allowlist.allowed_ids() == frozenset()
    assert str(NEWCOMER) in reply


async def test_an_allowed_guest_cannot_deny_anyone():
    reply, allowlist = await run(
        bot.on_deny, GUEST, [str(NEWCOMER)], FakeAllowlist({NEWCOMER})
    )

    assert allowlist.allowed_ids() == frozenset({NEWCOMER})
    assert "owner" in reply.lower()


async def test_the_owner_cannot_deny_themselves():
    reply, allowlist = await run(bot.on_deny, OWNER, [str(OWNER)])

    assert "owner" in reply.lower()


async def test_denying_an_environment_id_explains_that_it_is_not_possible():
    reply, allowlist = await run(bot.on_deny, OWNER, [str(GUEST)])

    assert "TELEGRAM_ALLOWED_USER_IDS" in reply


async def test_denying_an_id_that_was_never_allowed_says_so():
    reply, allowlist = await run(bot.on_deny, OWNER, [str(NEWCOMER)])

    assert "not allowed" in reply.lower()


async def test_the_owner_lists_both_sources():
    reply, _ = await run(bot.on_allowed, OWNER, [], FakeAllowlist({NEWCOMER}))

    assert str(OWNER) in reply
    assert str(GUEST) in reply
    assert str(NEWCOMER) in reply


async def test_an_allowed_guest_cannot_list_the_allowlist():
    reply, _ = await run(bot.on_allowed, GUEST, [], FakeAllowlist({NEWCOMER}))

    assert str(NEWCOMER) not in reply
    assert "owner" in reply.lower()
