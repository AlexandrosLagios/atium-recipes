---
name: vps-connection
description: Use when connecting to the Hetzner VPS that runs the recipeient bot, running a command on it, or deploying a new version of the bot.
---

# recipeient VPS Connection

## Requirements

- **Start Tailscale before you try SSH.** The VPS firewall (`ufw default deny
  incoming`, `allow in on tailscale0`) drops SSH on the public IP, so a direct
  connection to `91.99.107.239:22` times out by design. Start Tailscale with
  `open -a Tailscale`, then run
  `/Applications/Tailscale.app/Contents/MacOS/Tailscale up --accept-routes`.
  Keep `--accept-routes`; the node already has that non-default setting, and
  Tailscale refuses a command that omits it.
- **Port 22 is Tailscale SSH, not OpenSSH.** The banner reads
  `SSH-2.0-Tailscale`, Tailscale identity authenticates the session, and the key
  below is not consulted. A first connection in a while triggers a browser
  check. The session then produces no output for a minute or more, and the
  output starts with `To authenticate, visit:
  https://login.tailscale.com/a/...`. The session is pending, not hung. Run
  every remote command with `run_in_background: true` and read the output file.
- SSH key: `~/ZeroIchi.ssh`, in the home directory itself, not in `~/.ssh/`.
  Keep it for the OpenSSH fallback.
- User: `alex`
- Tailscale IP: `100.71.143.23`, hostname `ZeroIchi`
- Public IP: `91.99.107.239`. It serves HTTP and HTTPS only.

## Connect

```bash
ssh -i ~/ZeroIchi.ssh alex@100.71.143.23
```

## Deployment layout

`~/apps/recipeient/` is a `git clone` of
`https://github.com/AlexandrosLagios/atium-recipes`, and `.env` sits untracked
inside it. The repository is public, so the clone needs no credentials.
`.gitignore` lists `.env`, so `git pull` never touches it.

`git pull` deploys whatever branch the clone has checked out, and the cron
deploy fast-forwards to `origin/main`. Run `git branch --show-current` in the
clone before you deploy by hand, and switch to `main` if it reports anything
else.

The box also runs `~/apps/vlp/` and `~/apps/proxy/`. Do not change either one
from this project.

The bot has no ingress and no database. It uses Telegram long polling, so it
opens no inbound port. Do not add a Caddy site block, a hostname, a DNS record,
a TLS certificate, the `internal` network, or any port binding. `vlp` needs all
of them; this bot needs none of them.

Compose project: `recipeient`, one service named `recipebot`. The container is
`recipeient-recipebot-1`, so read the log with `docker compose logs` from the
deployment root rather than with a container name.

## Operations

Deploys are automatic. A user crontab entry runs `deploy/deploy.sh` every two
minutes. The script does nothing until `origin/main` moves, then it asks the
GitHub checks API whether every check run on that commit finished green, and
only then fast-forwards and rebuilds. A red or still-running commit is skipped
and retried on the next tick.

```bash
crontab -l                              # the schedule
tail -20 ~/apps/recipeient-deploy.log   # what it did, and what it is waiting for
```

The crontab uses `flock -n`, so a slow rebuild never overlaps the next tick.
The script needs no sudo and no GitHub token, because the repository is public.

Deploy by hand, for a commit the checks never covered:

```bash
cd ~/apps/recipeient && git pull && docker compose up -d --build
```

Read the log:

```bash
cd ~/apps/recipeient && docker compose logs --tail 50
```

`Application started` is the line that proves the bot polls Telegram.

Change a secret or a model id: edit `~/apps/recipeient/.env`, then run
`docker compose up -d` to recreate the container. A change to that file alone
does nothing until the container restarts.

## Common Mistakes

- Forgetting to start Tailscale first. The connection times out silently.
- Using the public IP for SSH. The connection times out the same way. The
  public IP serves only the published container ports.
- Looking for the key in `~/.ssh/`. It lives in `~/ZeroIchi.ssh`.
- Running a remote command that needs `sudo`. Non-interactive sudo fails with
  "interactive authentication is required", so the user must run anything
  privileged by hand.
- Leaving the local bot running after the server bot starts. Two processes then
  poll the same Telegram token, and Telegram gives each update to whichever
  process asks first. Half the replies disappear. Stop the local one.
- Matching the process with `pgrep -fl "python -m recipebot"`. That pattern
  finds nothing, because the command line holds the resolved interpreter path.
  Match on `recipebot` alone.
- Installing the deploy as a systemd unit or a `systemctl --user` timer. Both
  need sudo on this box: a system unit needs root, and a user timer needs
  `loginctl enable-linger alex`, which needs root too. `sudo` here always asks
  for a password, so the deploy runs from the user crontab instead.
- Registering a self-hosted GitHub Actions runner for this repository. The two
  runners on the box are scoped to `AlexandrosLagios/vlp` and
  `AlexandrosLagios/portofino`, which are private. `atium-recipes` is public, so
  a fork's pull request could run arbitrary code beside the two Postgres
  containers. The cron deploy exists to avoid exactly that.
