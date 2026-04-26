"""Sort YouTube live broadcasts on your channel into per-weekday playlists.

For each video on your channel that has `liveStreamingDetails.actualStartTime`,
this script computes the weekday of that start time in America/New_York and
adds the video to the playlist mapped to that weekday in `config.yaml`.

Default behavior is a dry run: nothing is modified unless `--apply` is passed.

Auth:
    Uses OAuth (scope: https://www.googleapis.com/auth/youtube) because adding
    items to playlists is a write operation. The first run opens a browser for
    consent; refresh tokens are stored in oauth_token.json next to this script.

    Set up OAuth credentials in Google Cloud Console:
      APIs & Services -> Credentials -> Create Credentials -> OAuth client ID
      Application type: "Desktop app"
      Download the JSON and save it as oauth_client_secret.json next to this
      script (or pass --client-secret PATH).

Usage:
    python sort_livestreams_into_playlists.py             # dry run
    python sort_livestreams_into_playlists.py --apply     # actually add
    python sort_livestreams_into_playlists.py --since 2024-01-01 --apply
    python sort_livestreams_into_playlists.py --limit 50  # cap videos scanned
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import yaml
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
CLIENT_SECRET_PATH = ROOT / "oauth_client_secret.json"
TOKEN_PATH = ROOT / "oauth_token.json"

SCOPES = ["https://www.googleapis.com/auth/youtube"]
TZ = ZoneInfo("America/New_York")

WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def load_schedule() -> dict[str, str]:
    """Return {weekday_name: playlist_id} from config.yaml."""
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    schedule = cfg.get("schedule") or {}
    out: dict[str, str] = {}
    for weekday, entry in schedule.items():
        if not isinstance(entry, dict):
            continue
        pid = entry.get("playlist_id")
        if pid:
            out[weekday.lower()] = pid
    return out


def get_authenticated_service(client_secret: Path):
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not client_secret.exists():
                print(
                    f"OAuth client secret not found at {client_secret}.\n"
                    "Create one in Cloud Console (Desktop app) and save it there.",
                    file=sys.stderr,
                )
                sys.exit(2)
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def get_uploads_playlist_id(youtube) -> str:
    resp = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = resp.get("items") or []
    if not items:
        raise RuntimeError("No channel found for the authenticated user.")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def iter_playlist_video_ids(youtube, playlist_id: str) -> Iterable[str]:
    page_token = None
    while True:
        resp = (
            youtube.playlistItems()
            .list(
                part="contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            )
            .execute()
        )
        for item in resp.get("items", []):
            vid = item.get("contentDetails", {}).get("videoId")
            if vid:
                yield vid
        page_token = resp.get("nextPageToken")
        if not page_token:
            return


def list_playlist_membership(youtube, playlist_id: str) -> dict[str, str]:
    """Return {videoId: playlistItemId} for items currently in the playlist."""
    out: dict[str, str] = {}
    page_token = None
    while True:
        resp = (
            youtube.playlistItems()
            .list(
                part="id,contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            )
            .execute()
        )
        for item in resp.get("items", []):
            vid = item.get("contentDetails", {}).get("videoId")
            if vid:
                out[vid] = item["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            return out


def chunked(seq: list, size: int) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def fetch_video_meta(youtube, video_ids: list[str]) -> list[dict]:
    out: list[dict] = []
    for chunk in chunked(video_ids, 50):
        resp = (
            youtube.videos()
            .list(part="snippet,liveStreamingDetails", id=",".join(chunk))
            .execute()
        )
        out.extend(resp.get("items", []))
    return out


def add_to_playlist(youtube, playlist_id: str, video_id: str) -> None:
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()


def parse_iso8601(s: str) -> datetime:
    # YouTube returns e.g. "2024-04-15T13:00:00Z"
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually add videos (default is dry-run).")
    parser.add_argument("--client-secret", type=Path, default=CLIENT_SECRET_PATH)
    parser.add_argument("--since", help="Only consider videos with actualStartTime on/after this date (YYYY-MM-DD).")
    parser.add_argument("--until", help="Only consider videos with actualStartTime before this date (YYYY-MM-DD).")
    parser.add_argument("--limit", type=int, help="Cap the number of uploads scanned.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    schedule = load_schedule()
    if not schedule:
        print("No `schedule:` entries with playlist_id in config.yaml.", file=sys.stderr)
        return 2
    print(f"Weekday -> playlist:")
    for wd in WEEKDAY_NAMES:
        if wd in schedule:
            print(f"  {wd:9s} -> {schedule[wd]}")

    since_dt = parse_iso8601(args.since + "T00:00:00Z") if args.since else None
    until_dt = parse_iso8601(args.until + "T00:00:00Z") if args.until else None

    youtube = get_authenticated_service(args.client_secret)

    uploads_id = get_uploads_playlist_id(youtube)
    print(f"\nUploads playlist: {uploads_id}")

    print("Listing all uploads...")
    upload_ids = list(iter_playlist_video_ids(youtube, uploads_id))
    print(f"  {len(upload_ids)} videos found.")
    if args.limit:
        upload_ids = upload_ids[: args.limit]
        print(f"  limited to first {len(upload_ids)}.")

    # Build membership cache for every target playlist (so we can skip already-sorted videos).
    print("\nReading current membership of target playlists...")
    membership: dict[str, set[str]] = {}
    for wd, pid in schedule.items():
        members = set(list_playlist_membership(youtube, pid).keys())
        membership[pid] = members
        print(f"  {wd:9s} ({pid}): {len(members)} videos")
    already_sorted: set[str] = set().union(*membership.values()) if membership else set()

    print("\nFetching video metadata (snippet + liveStreamingDetails)...")
    metas = fetch_video_meta(youtube, upload_ids)
    print(f"  fetched {len(metas)} entries.")

    counts: dict[str, int] = {wd: 0 for wd in WEEKDAY_NAMES}
    skipped_not_live = 0
    skipped_already = 0
    skipped_no_playlist = 0
    skipped_out_of_range = 0
    errors = 0
    actions: list[tuple[str, str, str, str]] = []  # (video_id, weekday, playlist_id, title)

    for meta in metas:
        vid = meta["id"]
        title = meta.get("snippet", {}).get("title", "")
        live = meta.get("liveStreamingDetails") or {}
        start = live.get("actualStartTime")
        if not start:
            skipped_not_live += 1
            continue
        start_utc = parse_iso8601(start)
        if since_dt and start_utc < since_dt:
            skipped_out_of_range += 1
            continue
        if until_dt and start_utc >= until_dt:
            skipped_out_of_range += 1
            continue
        weekday = WEEKDAY_NAMES[start_utc.astimezone(TZ).weekday()]
        target = schedule.get(weekday)
        if not target:
            skipped_no_playlist += 1
            if args.verbose:
                print(f"  - {vid}  {weekday:9s}  no target playlist  ({title!r})")
            continue
        if vid in already_sorted:
            skipped_already += 1
            if args.verbose:
                print(f"  - {vid}  {weekday:9s}  already in a target playlist  ({title!r})")
            continue
        actions.append((vid, weekday, target, title))
        counts[weekday] += 1

    print("\nPlanned additions:")
    for wd in WEEKDAY_NAMES:
        if wd in schedule:
            print(f"  {wd:9s}: +{counts[wd]}")
    print(
        f"\nSkipped: not-live={skipped_not_live}, already-sorted={skipped_already}, "
        f"no-playlist-for-weekday={skipped_no_playlist}, out-of-range={skipped_out_of_range}"
    )

    if not actions:
        print("\nNothing to do.")
        return 0

    if not args.apply:
        print("\n[dry-run] Re-run with --apply to actually add the above videos.")
        if args.verbose:
            for vid, wd, pid, title in actions[:25]:
                print(f"  + {vid}  {wd:9s} -> {pid}  ({title!r})")
            if len(actions) > 25:
                print(f"  ... and {len(actions) - 25} more")
        return 0

    print(f"\nAdding {len(actions)} videos...")
    for vid, wd, pid, title in actions:
        try:
            add_to_playlist(youtube, pid, vid)
            print(f"  + {vid}  {wd:9s} -> {pid}  ({title})")
            membership[pid].add(vid)
            already_sorted.add(vid)
        except HttpError as e:
            errors += 1
            print(f"  ! {vid}  {wd:9s} -> {pid}  FAILED: {e}", file=sys.stderr)

    print(f"\nDone. errors={errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
