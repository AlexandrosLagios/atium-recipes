from recipebot.users import UserRecord, UserStore


def a_record(**overrides) -> UserRecord:
    defaults = dict(
        telegram_user_id=1,
        notion_access_token="tok-1",
        notion_refresh_token="refresh-1",
        recipes_ds="ds-r",
        ingredients_ds="ds-i",
        workspace_name="Alex's Kitchen",
        connected_at=1_700_000_000,
    )
    return UserRecord(**{**defaults, **overrides})


def test_a_missing_user_returns_none(tmp_path):
    store = UserStore(str(tmp_path / "users.db"))

    assert store.get(1) is None


def test_a_saved_user_is_read_back(tmp_path):
    store = UserStore(str(tmp_path / "users.db"))

    store.save(a_record())

    assert store.get(1) == a_record()


def test_saving_the_same_id_again_replaces_the_row(tmp_path):
    store = UserStore(str(tmp_path / "users.db"))
    store.save(a_record())

    store.save(a_record(notion_access_token="tok-2"))

    assert store.get(1).notion_access_token == "tok-2"


def test_a_null_refresh_token_round_trips(tmp_path):
    store = UserStore(str(tmp_path / "users.db"))

    store.save(a_record(notion_refresh_token=None))

    assert store.get(1).notion_refresh_token is None


def test_delete_removes_the_row(tmp_path):
    store = UserStore(str(tmp_path / "users.db"))
    store.save(a_record())

    store.delete(1)

    assert store.get(1) is None


def test_delete_of_an_unknown_id_does_not_raise(tmp_path):
    store = UserStore(str(tmp_path / "users.db"))

    store.delete(999)


def test_a_second_store_on_the_same_path_sees_the_same_data(tmp_path):
    path = str(tmp_path / "users.db")
    UserStore(path).save(a_record())

    assert UserStore(path).get(1) == a_record()


def test_no_id_is_allowed_in_a_fresh_store(tmp_path):
    store = UserStore(str(tmp_path / "users.db"))

    assert store.allowed_ids() == frozenset()


def test_an_allowed_id_is_read_back(tmp_path):
    store = UserStore(str(tmp_path / "users.db"))

    store.allow(6529645381)

    assert store.allowed_ids() == frozenset({6529645381})


def test_allowing_the_same_id_twice_keeps_one_entry(tmp_path):
    store = UserStore(str(tmp_path / "users.db"))

    store.allow(7)
    store.allow(7)

    assert store.allowed_ids() == frozenset({7})


def test_deny_removes_the_id(tmp_path):
    store = UserStore(str(tmp_path / "users.db"))
    store.allow(7)

    store.deny(7)

    assert store.allowed_ids() == frozenset()


def test_deny_of_an_id_that_was_never_allowed_does_not_raise(tmp_path):
    store = UserStore(str(tmp_path / "users.db"))

    store.deny(999)


def test_the_allowlist_outlives_the_store_object(tmp_path):
    path = str(tmp_path / "users.db")
    UserStore(path).allow(7)

    assert UserStore(path).allowed_ids() == frozenset({7})


def test_allowing_an_id_does_not_connect_it(tmp_path):
    store = UserStore(str(tmp_path / "users.db"))

    store.allow(7)

    assert store.get(7) is None


def test_denying_an_id_leaves_its_notion_connection_alone(tmp_path):
    store = UserStore(str(tmp_path / "users.db"))
    store.save(a_record())
    store.allow(1)

    store.deny(1)

    assert store.get(1) == a_record()
