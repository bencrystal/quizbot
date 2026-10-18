"""Cross-post to Instagram Reels and Facebook (Reels for Shorts, a Page video for long-form).

Meta's Graph API pulls the video from a public URL, so the file is parked briefly as a release asset on the
(public) portfolio repo and removed once Meta has fetched it. Token: state/meta_token.json, written by
scripts/meta_auth.py (workflow `meta auth`), cached between runs like the YouTube token.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import requests

from ..config import Settings
from ..models import Episode

GRAPH = "https://graph.facebook.com/v23.0"
MEDIA_REPO = "bencrystal/portfolio_website"          # public; assets live under one rolling release
MEDIA_TAG = "quizbot-media"


class MetaError(RuntimeError):
    pass


def token(settings: Settings) -> dict | None:
    p = settings.state_dir / "meta_token.json"
    return json.loads(p.read_text()) if p.exists() else None


def _check(r: requests.Response) -> dict:
    try:
        d = r.json()
    except ValueError:
        raise MetaError(f"{r.status_code}: {r.text[:200]}")
    if "error" in d:
        e = d["error"]
        raise MetaError(f"{e.get('code')} {e.get('type')}: {e.get('message')}")
    return d


# ---- captions per platform --------------------------------------------------------------------
def caption(ep: Episode, platform: str) -> str:
    """Platform packaging: same video, different wrapper. No '#shorts', no 'subscribe'."""
    title = re.sub(r"\s*#shorts\s*$", "", ep.title).strip()
    pack_tag = "#" + re.sub(r"[^a-z0-9]", "", ep.pack)
    tags = ["#quiz", "#guessthesound", "#audioquiz", pack_tag, "#trivia"]
    if platform == "instagram":                       # the answer stays out of captions: no spoilers in the feed
        body = f"{title}\n\nHints are spoken, so you can play without looking. Score yourself in the comments."
        return body + "\n\nFollow for a new quiz every day.\n\n" + " ".join(tags)
    # facebook
    body = f"{title}\n\nHints are spoken, so you can play without looking. Score yourself in the comments."
    return body + "\n\n" + " ".join(tags)


# ---- public staging ---------------------------------------------------------------------------
def _gh(*args: str) -> str:
    env = dict(os.environ, GH_TOKEN=os.environ.get("PORTFOLIO_TOKEN", os.environ.get("GH_TOKEN", "")))
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, env=env).stdout


def stage(video: Path) -> str:
    """Upload the file as a release asset on the public media release; return its download URL."""
    try:
        _gh("release", "view", MEDIA_TAG, "-R", MEDIA_REPO)
    except subprocess.CalledProcessError:
        _gh("release", "create", MEDIA_TAG, "-R", MEDIA_REPO, "--title", "quizbot media (transient)",
            "--notes", "Transient staging for cross-posting. Files are removed after the platforms fetch them.")
    name = re.sub(r"[^A-Za-z0-9._-]", "-", video.name)
    _gh("release", "upload", MEDIA_TAG, f"{video}#{name}", "-R", MEDIA_REPO, "--clobber")
    url = f"https://github.com/{MEDIA_REPO}/releases/download/{MEDIA_TAG}/{name}"
    for _ in range(10):                                   # the asset takes a few seconds to become fetchable
        if requests.head(url, allow_redirects=True, timeout=30).status_code == 200:
            return url
        time.sleep(3)
    return url


def unstage(video: Path) -> None:
    name = re.sub(r"[^A-Za-z0-9._-]", "-", video.name)
    try:
        _gh("release", "delete-asset", MEDIA_TAG, name, "-R", MEDIA_REPO, "-y")
    except subprocess.CalledProcessError as e:
        print(f"[meta] could not remove staged asset {name}: {e.stderr[:120]}")


# ---- Instagram ---------------------------------------------------------------------------------
def post_instagram_reel(tok: dict, video_url: str, text: str, cover_url: str | None = None) -> str:
    ig = tok["ig_user_id"]
    body = {"media_type": "REELS", "video_url": video_url, "caption": text[:2200], "share_to_feed": "true",
            "access_token": tok["page_token"]}
    if cover_url:
        body["cover_url"] = cover_url
    container = _check(requests.post(f"{GRAPH}/{ig}/media", data=body, timeout=60))["id"]
    for _ in range(60):                                   # Meta transcodes; usually 20-90 s
        st = _check(requests.get(f"{GRAPH}/{container}", params={"fields": "status_code,status",
                                                                  "access_token": tok["page_token"]}, timeout=30))
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise MetaError(f"instagram container error: {st.get('status')}")
        time.sleep(5)
    else:
        raise MetaError("instagram container never finished")
    pub = _check(requests.post(f"{GRAPH}/{ig}/media_publish",
                               data={"creation_id": container, "access_token": tok["page_token"]}, timeout=60))
    return pub["id"]


# ---- Facebook ----------------------------------------------------------------------------------
def post_facebook_reel(tok: dict, video_path: Path, text: str) -> str:
    page = tok["page_id"]
    start = _check(requests.post(f"{GRAPH}/{page}/video_reels",
                                 data={"upload_phase": "start", "access_token": tok["page_token"]}, timeout=60))
    vid, upload_url = start["video_id"], start["upload_url"]
    size = video_path.stat().st_size
    with open(video_path, "rb") as f:
        r = requests.post(upload_url, headers={"Authorization": f"OAuth {tok['page_token']}", "offset": "0",
                                               "file_size": str(size)}, data=f, timeout=600)
    _check(r)
    _check(requests.post(f"{GRAPH}/{page}/video_reels",
                         data={"upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
                               "description": text[:5000], "access_token": tok["page_token"]}, timeout=60))
    return vid


def post_facebook_video(tok: dict, video_url: str, title: str, text: str) -> str:
    page = tok["page_id"]
    d = _check(requests.post(f"{GRAPH}/{page}/videos",
                             data={"file_url": video_url, "title": title[:255], "description": text[:5000],
                                   "access_token": tok["page_token"]}, timeout=120))
    return d["id"]


# ---- orchestration -----------------------------------------------------------------------------
def crosspost(settings: Settings, ep: Episode, video: Path, state) -> dict[str, str]:
    """Post to every Meta surface not yet done for this episode; records in the ledger. Returns {platform: id}."""
    tok = token(settings)
    if not tok:
        print("[meta] no token; skipping cross-post")
        return {}
    done = state.posts(ep.slug)
    out: dict[str, str] = {}
    staged_url = None
    try:
        if ep.format == "short":
            if "instagram" not in done and tok.get("ig_user_id"):
                staged_url = staged_url or stage(video)
                try:
                    pid = post_instagram_reel(tok, staged_url, caption(ep, "instagram"))
                    state.record_post(ep.slug, "instagram", pid)
                    out["instagram"] = pid
                    print(f"[meta] instagram reel {pid}")
                except Exception as e:
                    state.record_post(ep.slug, "instagram", None, error=str(e)[:300])
                    print(f"[meta] instagram failed: {str(e)[:200]}")
            if "facebook" not in done:
                try:
                    pid = post_facebook_reel(tok, video, caption(ep, "facebook"))
                    state.record_post(ep.slug, "facebook", pid)
                    out["facebook"] = pid
                    print(f"[meta] facebook reel {pid}")
                except Exception as e:
                    state.record_post(ep.slug, "facebook", None, error=str(e)[:300])
                    print(f"[meta] facebook failed: {str(e)[:200]}")
        else:
            if "facebook" not in done:
                staged_url = staged_url or stage(video)
                try:
                    pid = post_facebook_video(tok, staged_url, ep.title, caption(ep, "facebook"))
                    state.record_post(ep.slug, "facebook", pid)
                    out["facebook"] = pid
                    print(f"[meta] facebook video {pid}")
                except Exception as e:
                    state.record_post(ep.slug, "facebook", None, error=str(e)[:300])
                    print(f"[meta] facebook failed: {str(e)[:200]}")
    finally:
        if staged_url:
            time.sleep(10)                                # let the last fetch complete before pulling the asset
            unstage(video)
    return out
