"""Corpus study of viral trivia / quiz Shorts (study only; nothing is reused in our output).

For a set of search queries: find the most-viewed Shorts (YouTube Data API search.list + videos.list), pull
each one's auto thumbnails (i.ytimg.com 1/2/3.jpg = frames at ~25/50/75 % plus the poster frame), try the
transcript (youtube-transcript-api), and write everything to <out>/: corpus.json (one row per video with
stats, duration, title, transcript, channel size) and sheet.png (frames tiled, one row per video, labelled).
A Claude session reads the sheet and corpus.json from the `reference` branch.

  python scripts/study_corpus.py --out ref/study --queries "guess the pokemon cry" "name that pokemon" ...
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quizbot.config import Settings  # noqa: E402
from quizbot.upload import _creds    # noqa: E402


def iso_dur(s: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    h, mi, se = (int(x) if x else 0 for x in m.groups()) if m else (0, 0, 0)
    return h * 3600 + mi * 60 + se


def transcript(vid: str):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        tr = YouTubeTranscriptApi().fetch(vid, languages=["en", "en-US"])
        return [{"t": round(x.start, 1), "d": round(x.duration, 1), "text": x.text} for x in tr]
    except Exception as e:  # blocked / no captions: record why, keep going
        return {"error": str(e)[:160]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--queries", nargs="+", required=True)
    ap.add_argument("--per-query", type=int, default=12)
    ap.add_argument("--max-seconds", type=int, default=75)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from googleapiclient.discovery import build
    yt = build("youtube", "v3", credentials=_creds(Settings.load(), interactive=False))

    ids: dict[str, str] = {}
    for q in args.queries:
        r = yt.search().list(part="id", q=q, type="video", videoDuration="short", order="viewCount",
                             maxResults=args.per_query, relevanceLanguage="en", safeSearch="strict").execute()
        for it in r.get("items", []):
            ids.setdefault(it["id"]["videoId"], q)
    rows = []
    vids = list(ids)
    for k in range(0, len(vids), 50):
        r = yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(vids[k:k + 50])).execute()
        for v in r.get("items", []):
            sn, stt, cd = v["snippet"], v.get("statistics", {}), v.get("contentDetails", {})
            dur = iso_dur(cd.get("duration"))
            if dur > args.max_seconds or dur == 0:
                continue
            rows.append({"id": v["id"], "query": ids[v["id"]], "title": sn["title"], "channel": sn["channelTitle"],
                         "channel_id": sn["channelId"], "published": sn["publishedAt"], "duration": dur,
                         "views": int(stt.get("viewCount", 0)), "likes": int(stt.get("likeCount", 0)),
                         "comments": int(stt.get("commentCount", 0)), "tags": sn.get("tags", [])[:15],
                         "description": sn.get("description", "")[:400]})
    # channel size, for views-per-subscriber
    chans = sorted({r["channel_id"] for r in rows})
    subs = {}
    for k in range(0, len(chans), 50):
        r = yt.channels().list(part="statistics", id=",".join(chans[k:k + 50])).execute()
        for c in r.get("items", []):
            subs[c["id"]] = int(c["statistics"].get("subscriberCount", 0))
    for r in rows:
        r["channel_subs"] = subs.get(r["channel_id"], 0)
    rows.sort(key=lambda r: -r["views"])
    rows = rows[:40]

    # frames + transcripts
    font = ImageFont.truetype("DejaVuSans.ttf", 14) if Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf").exists() else ImageFont.load_default()
    tiles = []
    for r in rows:
        frames = []
        for name in ("1", "2", "3", "hqdefault"):
            try:
                resp = requests.get(f"https://i.ytimg.com/vi/{r['id']}/{name}.jpg", timeout=20)
                if resp.ok and len(resp.content) > 1000:
                    frames.append(Image.open(io.BytesIO(resp.content)).convert("RGB"))
            except Exception:
                pass
        r["frames"] = len(frames)
        r["transcript"] = transcript(r["id"])
        time.sleep(0.3)
        row_h = 240
        strip = Image.new("RGB", (4 * 330 + 10, row_h + 36), (20, 20, 24))
        for i, f in enumerate(frames):
            f = f.resize((320, int(320 * f.height / f.width)))
            strip.paste(f, (10 + i * 330, 36))
        d = ImageDraw.Draw(strip)
        label = f"{r['views']:,} views · {r['duration']}s · {r['channel']} ({r['channel_subs']:,} subs) · {r['title'][:70]}"
        d.text((10, 10), label, font=font, fill=(240, 240, 246))
        tiles.append(strip)
    if tiles:
        sheet = Image.new("RGB", (tiles[0].width, sum(t.height for t in tiles)), (20, 20, 24))
        y = 0
        for t in tiles:
            sheet.paste(t, (0, y))
            y += t.height
        sheet.save(out / "sheet.png")
    (out / "corpus.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    ok_tr = sum(1 for r in rows if isinstance(r["transcript"], list))
    print(f"{len(rows)} videos, {ok_tr} with transcripts, frames for {sum(1 for r in rows if r['frames'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
