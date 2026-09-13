"""Exit 0 when every GitHub check run for a commit finished successfully.

Reads the JSON body of /repos/{repo}/commits/{sha}/check-runs on stdin.
"""

import json
import sys

PASSING = ("success", "skipped", "neutral")


def is_green(payload: dict) -> bool:
    runs = payload["check_runs"]
    # An empty list means no workflow has registered a run for this commit yet,
    # so the commit is untested rather than green.
    return bool(runs) and all(
        run["status"] == "completed" and run["conclusion"] in PASSING for run in runs
    )


if __name__ == "__main__":
    sys.exit(0 if is_green(json.load(sys.stdin)) else 1)
