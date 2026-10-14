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


def authorize(settings: Settings) -> None:
    _creds(settings, interactive=True)
    print("YouTube auth OK; token saved to state/youtube_token.json")


def upload(settings: Settings, ep: Episode, result: RenderResult) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    yt = build("youtube", "v3", credentials=_creds(settings, interactive=False))
    status = {"privacyStatus": settings.youtube_privacy, "selfDeclaredMadeForKids": settings.made_for_kids}
    if settings.schedule_hours_ahead > 0:
        publish_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=settings.schedule_hours_ahead)
        status["privacyStatus"] = "private"            # required for scheduled publish
        status["publishAt"] = publish_at.isoformat().replace("+00:00", "Z")
    body = {
        "snippet": {"title": ep.title[:100], "description": ep.description[:5000],
                    "tags": ep.tags[:30], "categoryId": settings.youtube_category_id},
        "status": status,
    }
    media = MediaFileUpload(str(result.video), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        progress, resp = req.next_chunk()
        if progress:
            print(f"[upload] {int(progress.progress() * 100)}%")
    vid = resp["id"]
    try:
        yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(result.thumbnail))).execute()
    except Exception as e:  # thumbnails need a verified channel; don't fail the run over it
        print(f"[upload] thumbnail skipped: {e}")
    print(f"[upload] https://youtu.be/{vid}")
    return vid
