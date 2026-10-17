"""Export channel + per-video analytics to one JSON for the dashboard (bencrystal.com/quizbot).

Runs nightly in CI (analytics.yml) and commits the file to the portfolio site. Needs the YouTube token with
yt-analytics.readonly (scripts/reauth.py). Output schema is documented at the bottom of this file.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from googleapiclient.discovery import build  # noqa: E402

from quizbot.config import Settings  # noqa: E402
from quizbot.packs import PACK_NAMES, get_pack  # noqa: E402
from quizbot.upload import _creds  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("out/quizbot-analytics.json")
START = "2026-10-17"          # channel went public


def iso_dur(s: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    h, mi, se = (int(x or 0) for x in m.groups()) if m else (0, 0, 0)
    return h * 3600 + mi * 60 + se


def main() -> None:
    s = Settings.load()
    creds = _creds(s, interactive=False)
    yt = build("youtube", "v3", credentials=creds)
    yta = build("youtubeAnalytics", "v2", credentials=creds)
    today = dt.date.today().isoformat()
    packs = {n: get_pack(n, s) for n in PACK_NAMES}

    # ---- videos (Data API): everything on the channel
    ch = yt.channels().list(part="contentDetails,statistics,snippet", mine=True).execute()["items"][0]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, page = [], None
    while True:
        r = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50, pageToken=page).execute()
        ids += [i["contentDetails"]["videoId"] for i in r["items"]]
        page = r.get("nextPageToken")
        if not page:
            break
    videos = {}
    for k in range(0, len(ids), 50):
        for v in yt.videos().list(part="snippet,statistics,contentDetails,status",
                                  id=",".join(ids[k:k + 50])).execute()["items"]:
            sn, st = v["snippet"], v.get("statistics", {})
            tags = sn.get("tags") or []
            pack = next((t.split(":", 1)[1] for t in tags if t.startswith("pack:")), None)
            if pack is None:
                pack = next((n for n, p in packs.items() if p.noun.lower() in sn["title"].lower()), "unknown")
            variant = "coldopen" if "coldopen" in tags else "announce" if "announce" in tags else None
            theme = next((t for t in tags if t not in {"quiz", "audio quiz", "trivia", "guessing game", "coldopen", "announce"}
                          and not t.startswith("pack:") and t not in packs), "")
            videos[v["id"]] = {
                "id": v["id"], "title": sn["title"], "published": sn["publishedAt"], "pack": pack,
                "format": "short" if "#shorts" in sn["title"].lower() else "long", "variant": variant, "theme": theme,
                "duration": iso_dur(v["contentDetails"].get("duration")), "privacy": v.get("status", {}).get("privacyStatus"),
                "views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)), "comments": int(st.get("commentCount", 0)),
                "avd": None, "avp": None, "minutes": None, "subs": None,
            }

    # ---- per-video retention (Analytics API), 200 ids per query
    vids = list(videos)
    for k in range(0, len(vids), 200):
        chunk = vids[k:k + 200]
        rep = yta.reports().query(
            ids="channel==MINE", startDate=START, endDate=today, dimensions="video",
            metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,subscribersGained",
            filters="video==" + ",".join(chunk), maxResults=200, sort="-views").execute()
        cols = [c["name"] for c in rep.get("columnHeaders", [])]
        for row in rep.get("rows", []):
            d = dict(zip(cols, row))
            v = videos.get(d["video"])
            if v:
                v.update(views=int(d["views"]), minutes=round(d["estimatedMinutesWatched"], 1),
                         avd=round(d["averageViewDuration"], 1), avp=round(d["averageViewPercentage"], 1),
                         likes=int(d["likes"]), comments=int(d["comments"]), subs=int(d["subscribersGained"]))

    # ---- channel by day
    rep = yta.reports().query(ids="channel==MINE", startDate=START, endDate=today, dimensions="day",
                              metrics="views,estimatedMinutesWatched,subscribersGained,subscribersLost", sort="day").execute()
    cols = [c["name"] for c in rep.get("columnHeaders", [])]
    daily = [dict(zip(cols, r)) for r in rep.get("rows", [])]

    # ---- ledger (restored from the Actions cache when present)
    ledger = {"episodes": [], "used": {}}
    db = s.state_dir / "quizbot.db"
    if db.exists():
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        ledger["episodes"] = [dict(r) for r in c.execute(
            "SELECT slug, pack, title, theme, status, youtube_id, created_at FROM episodes ORDER BY created_at DESC LIMIT 400")]
        for r in c.execute("SELECT pack, fmt, COUNT(*) n FROM used_by_format GROUP BY pack, fmt"):
            ledger["used"].setdefault(r["pack"], {})[r["fmt"]] = r["n"]

    out = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "channel": {"title": ch["snippet"]["title"], "subscribers": int(ch["statistics"].get("subscriberCount", 0)),
                    "views": int(ch["statistics"].get("viewCount", 0)), "videos": int(ch["statistics"].get("videoCount", 0)),
                    "daily": daily},
        "packs": {n: {"display_name": p.display_name, "noun": p.noun} for n, p in packs.items()},
        "videos": sorted(videos.values(), key=lambda v: v["published"], reverse=True),
        "ledger": ledger,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=0) + "\n")
    print(f"wrote {OUT}: {len(videos)} videos, {len(daily)} days")


if __name__ == "__main__":
    main()

# Schema
# generated: ISO time
# channel: {title, subscribers, views, videos, daily: [{day, views, estimatedMinutesWatched, subscribersGained, subscribersLost}]}
# packs: {name: {display_name, noun}}
# videos: [{id, title, published, pack, format(short|long), variant(coldopen|announce|null), theme, duration(s),
#           privacy, views, likes, comments, avd(avg view duration s), avp(avg % viewed), minutes, subs}]
# ledger: {episodes: [{slug, pack, title, theme, status, youtube_id, created_at}], used: {pack: {short: n, long: n}}}
