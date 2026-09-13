import json
import pathlib
import subprocess
import sys

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "checks_green.py"


def verdict(*runs: dict) -> int:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps({"total_count": len(runs), "check_runs": list(runs)}),
        text=True,
    ).returncode


def a_run(status: str = "completed", conclusion: str = "success") -> dict:
    return {"status": status, "conclusion": conclusion}


def test_every_check_passing_is_green():
    assert verdict(a_run(), a_run(conclusion="skipped")) == 0


def test_one_failing_check_is_not_green():
    assert verdict(a_run(), a_run(conclusion="failure")) == 1


def test_a_check_still_running_is_not_green():
    assert verdict(a_run(status="in_progress", conclusion=None)) == 1


def test_a_commit_with_no_check_run_is_not_green():
    assert verdict() == 1
