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
