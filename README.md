# fitness-slack-bot

Picks a random YouTube video from a weekday-specific playlist (HIIT, Pilates, Tabata, Stretching, ...)
and posts it to Slack as you. Avoids re-picking videos used in the last `history_days` days.

## Setup

```powershell
cd "C:\Users\Preston Connors\Code\fitness-slack-bot"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# edit .env and fill in YOUTUBE_API_KEY + SLACK_USER_TOKEN
```

### Tokens

- **YouTube API key** – Google Cloud Console → APIs & Services → enable "YouTube Data API v3" → Credentials → API key.
- **Slack user token (`xoxp-...`)** – https://api.slack.com/apps → Create App → OAuth & Permissions →
  add **User Token Scopes**: `chat:write` (and `channels:read` if posting to a public channel by name).
  Install the app to your workspace and copy the **User OAuth Token**.

### Configure playlists

Edit `config.yaml`. Each weekday maps to a fitness format and a YouTube playlist ID.

## Usage

```powershell
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

## Schedule it

Windows Task Scheduler → run daily at e.g. 8:55am:

- Program: `C:\Users\Preston Connors\Code\fitness-slack-bot\.venv\Scripts\python.exe`
- Arguments: `daily_fitness.py`
- Start in: `C:\Users\Preston Connors\Code\fitness-slack-bot`
