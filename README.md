# fitness-slack-bot

Picks a random YouTube video from a weekday-specific playlist (HIIT, Pilates, Tabata, Stretching, ...)
and posts it to Slack as you. Avoids re-picking videos used in the last `history_days` days.

Target runtime: **Ubuntu 24.04 LTS** (also works on Windows / macOS).

## Setup (Ubuntu 24.04)

```bash
cd ~/fitness-slack-bot
sudo apt update
sudo apt install -y python3-venv python3-pip
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
# edit .env and fill in YOUTUBE_API_KEY + SLACK_USER_TOKEN
nano .env
```

### Tokens

- **YouTube API key** – Google Cloud Console → APIs & Services → enable "YouTube Data API v3" → Credentials → API key.
- **Slack user token (`xoxp-...`)** – https://api.slack.com/apps → Create App → OAuth & Permissions →
  add **User Token Scopes**: `chat:write` (and `channels:read` if posting to a public channel by name).
  Install the app to your workspace and copy the **User OAuth Token**.

### Configure playlists & message

Edit [config.yaml](config.yaml). Each weekday maps to a fitness format and a YouTube playlist ID.
The Slack message format is controlled by `message_template` in the same file.

## Usage

```bash
source .venv/bin/activate

# Preview today's pick without posting
python daily_fitness.py --dry-run

# Pick + post to Slack
python daily_fitness.py

# Force a specific weekday's playlist
python daily_fitness.py --weekday wednesday
```

`history.json` is written next to the script and tracks which video IDs were used and when.
Videos within `history_days` (default 30) are excluded; if every video in a playlist is "recent",
the script falls back to picking from the full playlist.

## Schedule it on Ubuntu

### Option A — cron (simplest)

```bash
crontab -e
```

Add a line to run weekdays at 11:00 local time:

```cron
0 11 * * 1-5 cd /home/preston/fitness-slack-bot && /home/preston/fitness-slack-bot/.venv/bin/python daily_fitness.py >> /home/preston/fitness-slack-bot/cron.log 2>&1
```

Make sure the server's timezone is correct:

```bash
timedatectl                                       # check
sudo timedatectl set-timezone America/New_York    # adjust as needed
```

### Option B — systemd user timer (recommended)

Unit files are already in this repo at [deploy/fitness-slack-bot.service](deploy/fitness-slack-bot.service)
and [deploy/fitness-slack-bot.timer](deploy/fitness-slack-bot.timer). They use `%h` so they
work for any user as long as the repo is checked out at `~/fitness-slack-bot`.

Install, enable, start:

```bash
mkdir -p ~/.config/systemd/user
install -m 0644 deploy/fitness-slack-bot.service ~/.config/systemd/user/fitness-slack-bot.service
install -m 0644 deploy/fitness-slack-bot.timer   ~/.config/systemd/user/fitness-slack-bot.timer

systemctl --user daemon-reload
systemctl --user enable --now fitness-slack-bot.timer
loginctl enable-linger "$USER"     # so the timer runs when you're not logged in
systemctl --user list-timers | grep fitness
```

Run it once now to verify:

```bash
systemctl --user start fitness-slack-bot.service
journalctl --user -u fitness-slack-bot.service -n 50
```

Edit `deploy/fitness-slack-bot.timer`'s `OnCalendar=` line to change the schedule.
The shipped value `Mon..Fri 11:00 America/New_York` runs at 11:00 Eastern regardless of
the server's system timezone (handles DST automatically). After editing, re-install and:

```bash
install -m 0644 deploy/fitness-slack-bot.timer ~/.config/systemd/user/fitness-slack-bot.timer
systemctl --user daemon-reload
systemctl --user restart fitness-slack-bot.timer
```

View logs anytime:

```bash
journalctl --user -u fitness-slack-bot.service -n 50
```

## Optional: also update an nginx `/livestream` redirect

If you want today's pick to also be reflected at e.g. `https://prestonconnors.com/livestream`,
the script can rewrite the redirect URL in your nginx site config and reload nginx after each pick.

Because this requires root, all privileged work is done by a single small wrapper:
[deploy/update_livestream_redirect.sh](deploy/update_livestream_redirect.sh).
It validates the URL, swaps it into the `rewrite ^/livestream$ ... redirect;` line,
runs `nginx -t`, and reloads nginx (rolling back the file on test failure).

### One-time setup

1. Edit the `SITE_FILE` and `PATTERN` variables at the top of
   `deploy/update_livestream_redirect.sh` if your site config path or rewrite
   line differ from the defaults.
2. Install the wrapper as root-owned and grant your user passwordless sudo for
   that **one** command:

   ```bash
   sudo install -m 0755 -o root -g root \
     deploy/update_livestream_redirect.sh \
     /usr/local/sbin/update_livestream_redirect.sh

   echo 'preston ALL=(root) NOPASSWD: /usr/local/sbin/update_livestream_redirect.sh' \
     | sudo tee /etc/sudoers.d/fitness-slack-bot
   sudo chmod 0440 /etc/sudoers.d/fitness-slack-bot
   sudo visudo -c   # validate
   ```

3. In `config.yaml`, set `nginx.enabled: true` (it already is by default).
4. Test it manually before relying on it:

   ```bash
   sudo -n /usr/local/sbin/update_livestream_redirect.sh https://www.youtube.com/watch?v=dQw4w9WgXcQ
   curl -sI https://prestonconnors.com/livestream | grep -i ^location
   ```

If the helper isn't installed (or sudo isn't configured), the Python script
prints a warning and continues — the Slack post still happens.

## One-time backfill: sort existing livestreams into the playlists

If you have hundreds of past live broadcasts on your channel and want them
auto-sorted into the per-weekday playlists used above, run
[sort_livestreams_into_playlists.py](sort_livestreams_into_playlists.py).
It groups each live broadcast by the weekday of its `actualStartTime`
(in America/New_York) and adds it to the matching playlist from `config.yaml`.

This script needs OAuth (writes to your playlists), not just the API key:

1. Cloud Console → APIs & Services → Credentials → Create Credentials →
   **OAuth client ID** → *Desktop app*. Download the JSON.
2. Save it as `oauth_client_secret.json` next to the script
   (already in `.gitignore`).
3. Run a dry-run first:

   ```bash
   source .venv/bin/activate
   python sort_livestreams_into_playlists.py --verbose
   ```

   The first run opens a browser to consent; refresh tokens are stored in
   `oauth_token.json`. On a headless server, you can run the OAuth step
   locally first and copy `oauth_token.json` to the server.
4. When the dry-run plan looks right, apply it:

   ```bash
   python sort_livestreams_into_playlists.py --apply
   ```

Useful flags: `--since 2024-01-01`, `--until 2025-01-01`, `--limit 50`.

Videos already in any of the target playlists are skipped, so re-running is safe.

## Troubleshooting

- `YOUTUBE_API_KEY not set` — populate `.env` (copied from `.env.example`).
- `Slack error: not_in_channel` — invite yourself / the app to `#fitness`, or set `SLACK_CHANNEL` to the channel ID.
- Wrong weekday picked — the script uses the system's local time. Check `timedatectl`.
