import pathlib
import subprocess

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "allow-telegram-user.sh"
NEW_ID = "6529645381"


def edit(tmp_path: pathlib.Path, allowlist_line: str):
    env_file = tmp_path / ".env"
    env_file.write_text(f"TELEGRAM_TOKEN=t\n{allowlist_line}\nLLM_PROVIDER=gemini\n")
    done = subprocess.run(
        ["bash", str(SCRIPT), "--here", NEW_ID, str(env_file)],
        capture_output=True,
        text=True,
    )
    return done, env_file.read_text()


def test_a_new_id_joins_the_allowlist(tmp_path):
    done, text = edit(tmp_path, "TELEGRAM_ALLOWED_USER_IDS=111,222")
    assert done.stdout.strip() == "changed"
    assert "TELEGRAM_ALLOWED_USER_IDS=111,222,6529645381\n" in text


def test_an_id_already_allowed_is_left_alone(tmp_path):
    done, text = edit(tmp_path, "TELEGRAM_ALLOWED_USER_IDS=111,6529645381,222")
    assert done.stdout.strip() == "unchanged"
    assert "TELEGRAM_ALLOWED_USER_IDS=111,6529645381,222\n" in text


def test_a_missing_allowlist_fails_instead_of_writing(tmp_path):
    done, text = edit(tmp_path, "TELEGRAM_ALLOWED_USER_ID=111")
    assert done.returncode == 1
    assert NEW_ID not in text


def test_an_id_that_is_not_a_number_never_reaches_the_vps():
    done = subprocess.run(
        ["bash", str(SCRIPT), "111; rm -rf /"],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 2
