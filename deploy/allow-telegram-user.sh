#!/usr/bin/env bash
# Add a Telegram user id to the deployed bot's allowlist, then restart the bot.
set -euo pipefail

HOST="${RECIPEIENT_HOST:-alex@100.71.143.23}"
REPO_DIR="${REPO_DIR:-apps/recipeient}"
KEY=TELEGRAM_ALLOWED_USER_IDS

# --here runs on the VPS. The branch below pipes this same file to bash there,
# so the allowlist edit stays one script and stays testable on a local .env.
if [ "${1:-}" = "--here" ]; then
    id="$2"
    env_file="$3"
    current=$(sed -n "s/^$KEY=//p" "$env_file")
    if [ -z "$current" ]; then
        echo "$env_file has no $KEY to extend" >&2
        exit 1
    fi
    case ",$current," in
        *",$id,"*)
            echo unchanged
            exit 0
            ;;
    esac
    sed -i.bak "s/^$KEY=.*/&,$id/" "$env_file"
    echo changed
    exit 0
fi

id="${1:-}"
case "$id" in
    "" | *[!0-9]*)
        echo "usage: $0 <telegram-user-id>" >&2
        exit 2
        ;;
esac

# Take the last line only: a first Tailscale SSH in a while prefixes the
# session with an authentication notice.
result=$(ssh "$HOST" "bash -s -- --here $id \$HOME/$REPO_DIR/.env" < "$0" | tail -1)
echo "$id: $result"
[ "$result" = changed ] || exit 0

# The container reads .env once at start, so the new id needs a recreate.
ssh "$HOST" "cd \$HOME/$REPO_DIR && docker compose up -d && sleep 5 && docker compose logs --tail 5"
