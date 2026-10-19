"""YouTube Data API v3 upload. One-time interactive auth (`quizbot auth`), then refresh tokens
keep it unattended indefinitely (as long as the OAuth app is in 'production', not 'testing').

Quota: default 10,000 units/day; videos.insert costs ~1,600, thumbnails.set 50 → ~6 uploads/day.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from ..config import Settings
from ..models import Episode, RenderResult

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]


def _creds(settings: Settings, interactive: bool):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = settings.state_dir / "youtube_token.json"
    secret_path = settings.root / "config" / "client_secret.json"
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
    if not creds or not creds.valid:
        if not interactive:
            raise SystemExit("YouTube auth missing/expired. Run `python -m quizbot auth` once, interactively.")
        if not secret_path.exists():
            raise SystemExit(f"Put your OAuth client secret at {secret_path} (see README).")
        flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
        creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return creds


def next_publish_time(settings: Settings) -> dt.datetime | None:
    """publish_time "HH:MM" → next local occurrence at least 1h away; else fixed offset; else None."""
    now = dt.datetime.now().astimezone()
    if settings.publish_time:
        h, m = (int(x) for x in settings.publish_time.split(":"))
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        while t < now + dt.timedelta(hours=1):
            t += dt.timedelta(days=1)
        return t
    if settings.schedule_hours_ahead > 0:
        return now + dt.timedelta(hours=settings.schedule_hours_ahead)
    return None


def authorize(settings: Settings) -> None:
    _creds(settings, interactive=True)
    print("YouTube auth OK; token saved to state/youtube_token.json")


def set_thumbnail(yt, vid: str, thumb) -> bool:
    """Custom thumbnail; YouTube allows only a handful of thumbnail changes per day, so on a 429 a marker file is
    left next to the thumbnail and `retry-uploads` tries again on the next run (otherwise YouTube keeps an auto
    frame, which for us is often the reveal — a spoiler)."""
    from pathlib import Path
    thumb = Path(thumb)
    pending = thumb.with_suffix(".pending")
    try:
        yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumb))).execute()
        pending.unlink(missing_ok=True)
        return True
    except Exception as e:  # rate limit, or an unverified channel; never fail the run over it
        print(f"[upload] thumbnail not set ({str(e)[:100]}); will retry next run")
        pending.write_text(vid)
        return False


def upload(settings: Settings, ep: Episode, result: RenderResult) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    yt = build("youtube", "v3", credentials=_creds(settings, interactive=False))
    status = {"privacyStatus": settings.youtube_privacy, "selfDeclaredMadeForKids": settings.made_for_kids}
    publish_at = next_publish_time(settings)
    if publish_at and settings.youtube_privacy == "public":
        status["privacyStatus"] = "private"            # YouTube requires private + publishAt for scheduling
        status["publishAt"] = publish_at.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        print(f"[upload] scheduled for {publish_at:%Y-%m-%d %H:%M %Z}")
    body = {
        "snippet": {"title": ep.title[:100], "description": ep.description[:5000],
                    "tags": ep.tags[:30], "categoryId": settings.youtube_category_id},
        "status": status,
    }
    media = MediaFileUpload(str(result.video), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp, errors = None, 0
    while resp is None:
        try:
            progress, resp = req.next_chunk(num_retries=3)
        except Exception as e:           # resumable upload: a chunk timeout isn't fatal, resume where we were
            errors += 1
            if errors > 5:
                raise
            print(f"[upload] chunk failed ({str(e)[:80]}); retrying ({errors}/5)")
            import time; time.sleep(5 * errors)
            continue
        if progress:
            print(f"[upload] {int(progress.progress() * 100)}%")
    vid = resp["id"]
    set_thumbnail(yt, vid, result.thumbnail)
    try:
        add_to_playlist(yt, vid, playlist_title(ep, settings))
    except Exception as e:  # organisation only; never fail a run over it
        print(f"[upload] playlist skipped: {e}")
    print(f"[upload] https://youtu.be/{vid}")
    return vid


def playlist_title(ep: Episode, settings: Settings) -> str:
    """One playlist per pack and format, e.g. 'Guess That Pokémon (Shorts)' / 'Guess That Pokémon (Long Form)'."""
    from ..packs import get_pack
    name = get_pack(ep.pack, settings).display_name
    return f"{name} ({'Shorts' if ep.format == 'short' else 'Long Form'})"


def add_to_playlist(yt, video_id: str, title: str) -> None:
    """Find the channel playlist with this title (create it if missing) and append the video."""
    pid, page = None, None
    while pid is None:
        r = yt.playlists().list(part="snippet", mine=True, maxResults=50, pageToken=page).execute()
        pid = next((p["id"] for p in r.get("items", []) if p["snippet"]["title"] == title), None)
        page = r.get("nextPageToken")
        if not page:
            break
    if pid is None:
        r = yt.playlists().insert(part="snippet,status", body={
            "snippet": {"title": title, "description": "Audio quizzes — play with your eyes closed."},
            "status": {"privacyStatus": "public"}}).execute()
        pid = r["id"]
        print(f"[upload] created playlist '{title}'")
    import time
    for attempt in range(4):             # playlistItems.insert throws spurious 409 SERVICE_UNAVAILABLE; retry
        try:
            yt.playlistItems().insert(part="snippet", body={
                "snippet": {"playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
            break
        except Exception as e:
            if attempt == 3 or "409" not in str(e):
                raise
            time.sleep(3 * (attempt + 1))
    print(f"[upload] added to playlist '{title}'")
