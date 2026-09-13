#!/usr/bin/env bash
# Deploy origin/main onto the VPS once GitHub reports every check green.
# Cron runs this every two minutes. It does nothing until the remote moves.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/apps/recipeient}"
REPO="${REPO:-AlexandrosLagios/atium-recipes}"

# Bash reads a script while it runs, and this script pulls its own directory.
# Keep every statement in a function, so the whole file parses before the pull.
checks_are_green() {
    curl -fsSL -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/$REPO/commits/$1/check-runs" |
        python3 "$REPO_DIR/deploy/checks_green.py"
}

main() {
    cd "$REPO_DIR"
    git fetch --quiet origin main
    local target
    target=$(git rev-parse origin/main)
    if [ "$(git rev-parse HEAD)" = "$target" ]; then
        return 0
    fi
    if ! checks_are_green "$target"; then
        echo "$(date -Is) waiting on checks for $target"
        return 0
    fi
    git merge --ff-only origin/main
    docker compose up -d --build
    echo "$(date -Is) deployed $target"
}

main "$@"
