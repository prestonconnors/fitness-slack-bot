"""Pick a YouTube fitness video for today's weekday and post it to Slack.

Run:
    python daily_fitness.py            # pick + post
    python daily_fitness.py --dry-run  # pick + print, do not post
    python daily_fitness.py --weekday wednesday  # override weekday
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import requests
import yaml
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
HISTORY_PATH = ROOT / "history.json"

YT_PLAYLIST_ITEMS = "https://www.googleapis.com/youtube/v3/playlistItems"
YT_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"


@dataclass
class Video:
    video_id: str
    title: str
    channel_title: str
    duration_seconds: int

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def duration_human(self) -> str:
        m, s = divmod(self.duration_seconds, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_history() -> dict:
    if not HISTORY_PATH.exists():
        return {}
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_history(history: dict) -> None:
    HISTORY_PATH.write_text(json.dumps(history, indent=2, sort_keys=True), encoding="utf-8")


def prune_history(history: dict, history_days: int) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=history_days)
    return {
        vid: ts
        for vid, ts in history.items()
        if _parse_ts(ts) and _parse_ts(ts) >= cutoff  # type: ignore[operator]
    }


def _parse_ts(ts: str):
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def fetch_playlist_video_ids(api_key: str, playlist_id: str) -> list[str]:
    ids: list[str] = []
    page_token = None
    while True:
        params = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,
            "key": api_key,
        }
        if page_token:
            params["pageToken"] = page_token
        r = requests.get(YT_PLAYLIST_ITEMS, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        for item in data.get("items", []):
            vid = item.get("contentDetails", {}).get("videoId")
            if vid:
                ids.append(vid)
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return ids


def fetch_video_details(api_key: str, video_ids: Iterable[str]) -> dict[str, Video]:
    results: dict[str, Video] = {}
    ids = list(video_ids)
    for i in range(0, len(ids), 50):
        chunk = ids[i : i + 50]
        params = {
            "part": "snippet,contentDetails,status",
            "id": ",".join(chunk),
            "key": api_key,
        }
        r = requests.get(YT_VIDEOS, params=params, timeout=30)
        r.raise_for_status()
        for item in r.json().get("items", []):
            status = item.get("status", {})
            # Skip unavailable / private / non-embeddable
            if status.get("privacyStatus") not in ("public", "unlisted"):
                continue
            snippet = item.get("snippet", {})
            content = item.get("contentDetails", {})
            results[item["id"]] = Video(
                video_id=item["id"],
                title=snippet.get("title", "(untitled)"),
                channel_title=snippet.get("channelTitle", ""),
                duration_seconds=_iso8601_duration_to_seconds(content.get("duration", "PT0S")),
            )
    return results


def _iso8601_duration_to_seconds(iso: str) -> int:
    # Minimal ISO 8601 duration parser for YouTube format e.g. PT1H2M3S
    import re

    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def pick_video(
    api_key: str,
    playlist_id: str,
    history: dict,
    history_days: int,
    rng: random.Random,
) -> Video:
    ids = fetch_playlist_video_ids(api_key, playlist_id)
    if not ids:
        raise RuntimeError(f"Playlist {playlist_id} returned no videos.")

    cutoff = datetime.now(timezone.utc) - timedelta(days=history_days)
    recent = {
        vid for vid, ts in history.items() if (_parse_ts(ts) and _parse_ts(ts) >= cutoff)  # type: ignore[operator]
    }
    eligible_ids = [v for v in ids if v not in recent]
    if not eligible_ids:
        # Everything is "recent"; reset for this playlist by allowing any.
        eligible_ids = ids

    details = fetch_video_details(api_key, eligible_ids)
    if not details:
        raise RuntimeError("No eligible videos available (all unavailable/private).")

    chosen_id = rng.choice(list(details.keys()))
    return details[chosen_id]


def build_slack_message(format_name: str, weekday_name: str, video: Video) -> str:
    return (
        f":muscle: *{weekday_name} {format_name}* :muscle:\n"
        f"<{video.url}|{video.title}> ({video.duration_human}) — {video.channel_title}\n"
        f"Join me in #fitness — press play together!"
    )


def post_to_slack(token: str, channel: str, text: str) -> dict:
    client = WebClient(token=token)
    try:
        resp = client.chat_postMessage(channel=channel, text=text, unfurl_links=True)
    except SlackApiError as e:
        raise RuntimeError(f"Slack error: {e.response.get('error')}") from e
    return resp.data  # type: ignore[return-value]


def main() -> int:
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Do not post to Slack.")
    parser.add_argument("--weekday", help="Override weekday (e.g. monday).")
    parser.add_argument("--seed", type=int, help="Random seed for reproducibility.")
    args = parser.parse_args()

    config = load_config()
    history_days = int(config.get("history_days", 30))
    schedule = config.get("schedule", {})
    default = config.get("default")

    weekday = (args.weekday or datetime.now().strftime("%A")).lower()
    entry = schedule.get(weekday) or default
    if not entry:
        print(f"No playlist configured for {weekday} and no default. Skipping.")
        return 0

    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        print("YOUTUBE_API_KEY not set. Copy .env.example to .env and fill it in.", file=sys.stderr)
        return 2

    history = prune_history(load_history(), history_days)
    rng = random.Random(args.seed)

    video = pick_video(
        api_key=api_key,
        playlist_id=entry["playlist_id"],
        history=history,
        history_days=history_days,
        rng=rng,
    )

    text = build_slack_message(
        format_name=entry["format"],
        weekday_name=weekday.capitalize(),
        video=video,
    )

    print(text)

    if args.dry_run:
        print("\n[dry-run] Not posting to Slack and not updating history.")
        return 0

    token = os.environ.get("SLACK_USER_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL", "#fitness")
    if not token:
        print("SLACK_USER_TOKEN not set.", file=sys.stderr)
        return 2

    resp = post_to_slack(token, channel, text)
    print(f"\nPosted to Slack: ts={resp.get('ts')} channel={resp.get('channel')}")

    history[video.video_id] = datetime.now(timezone.utc).isoformat()
    save_history(history)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
