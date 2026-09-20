---
description: Allow a Telegram user id on the deployed recipeient bot
argument-hint: <telegram-user-id>
allowed-tools: Bash
---

Add Telegram user id `$1` to the deployed bot's allowlist.

1. Check Tailscale, because SSH to the VPS answers on Tailscale only. Run
   `/Applications/Tailscale.app/Contents/MacOS/Tailscale status`. If it reports
   a logged-out or starting node, run `open -a Tailscale` and then
   `/Applications/Tailscale.app/Contents/MacOS/Tailscale up --accept-routes`.
2. Run `deploy/allow-telegram-user.sh $1` with `run_in_background: true`. A
   first Tailscale SSH in a while prints an authentication URL and then waits,
   so a foreground call looks hung.
3. Report the new allowlist and whether the log shows `Application started`.

The script is idempotent. An id that is already allowed prints `unchanged` and
skips the restart.
