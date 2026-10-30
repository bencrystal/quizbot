"""One-shot channel branding via the YouTube API (run by channel.yml): description + keywords, a generated
banner, and one channel-page section per playlist. Idempotent — safe to re-run after adding packs."""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from googleapiclient.discovery import build  # noqa: E402
from googleapiclient.http import MediaFileUpload  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from quizbot.config import Settings  # noqa: E402
from quizbot.packs import PACK_NAMES, get_pack  # noqa: E402
from quizbot.upload import _creds  # noqa: E402

DESCRIPTION = """Audio quizzes you can play with your eyes closed.

Hear a sound, guess what it is, get a hint, guess again. Every hint is spoken, so you can play while walking, driving or doing the dishes.

Right now: Name That Pokémon (name it from its cry) and Name That Bird (name it from its call). More quizzes on the way: video-game sounds and name-that-tune.

New Shorts every day · a full 10-round episode for each quiz every week.
Score yourself and drop it in the comments.
"""
KEYWORDS = '"audio quiz" "guess the sound" pokemon "pokemon cry" "bird calls" "bird quiz" quiz trivia "guessing game" shorts'


def banner(path: Path) -> Path:
    """2560×1440 banner; the safe area (1546×423, centred) carries the wordmark and tagline."""
    W, H = 2560, 1440
    img = Image.new("RGB", (W, H), (18, 18, 28))
    d = ImageDraw.Draw(img)
    # soft radial glow
    for r in range(1800, 0, -8):                      # falls off past the canvas edge: no visible rim
        t = 1 - r / 1800
        a = int(18 + 44 * t * t)
        d.ellipse((W // 2 - r, H // 2 - r, W // 2 + r, H // 2 + r), fill=(a, a, a + 10))
    # waveform band across the safe area
    cx, cy = W // 2, H // 2
    accent = (255, 203, 5)
    for i in range(-24, 25):
        v = abs(math.sin(i * 0.55)) * 0.7 + abs(math.sin(i * 1.9)) * 0.3
        h = 14 + 150 * v
        x = cx + 60 + i * 22
        d.rounded_rectangle((x - 5, cy + 40 - h / 2, x + 5, cy + 40 + h / 2), radius=5,
                            fill=accent if abs(i) < 9 else (120, 118, 140))
    # disc with "?" on the left of the band
    r = 110
    d.ellipse((cx - 700 - r, cy - r, cx - 700 + r, cy + r), fill=(10, 10, 14))
    try:
        big = ImageFont.truetype("DejaVuSans-Bold.ttf", 150)
        mid = ImageFont.truetype("DejaVuSans-Bold.ttf", 96)
        small = ImageFont.truetype("DejaVuSans.ttf", 40)
    except OSError:
        big = mid = small = ImageFont.load_default()
    d.text((cx - 700, cy), "?", font=big, fill=accent, anchor="mm")
    d.text((cx + 60, cy - 120), "PLAY IT BY EAR", font=mid, fill=(240, 239, 236), anchor="mm")
    d.text((cx + 60, cy + 170), "Audio quizzes you can play with your eyes closed", font=small, fill=(195, 194, 183), anchor="mm")
    img.save(path, quality=92)
    return path


def main() -> None:
    s = Settings.load()
    yt = build("youtube", "v3", credentials=_creds(s, interactive=False))
    ch = yt.channels().list(part="id,brandingSettings", mine=True).execute()["items"][0]
    cid = ch["id"]

    # banner
    out = s.out_dir / "banner.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    banner(out)
    b = yt.channelBanners().insert(media_body=MediaFileUpload(str(out), mimetype="image/jpeg")).execute()
    branding = ch["brandingSettings"]
    branding.setdefault("channel", {}).update({"description": DESCRIPTION.strip(), "keywords": KEYWORDS,
                                               "unsubscribedTrailer": branding.get("channel", {}).get("unsubscribedTrailer", "")})
    branding.setdefault("image", {})["bannerExternalUrl"] = b["url"]
    yt.channels().update(part="brandingSettings", body={"id": cid, "brandingSettings": branding}).execute()
    print("description, keywords and banner updated")

    # channel sections: one per playlist, Shorts before Long Form, packs in rotation order
    playlists = {p["snippet"]["title"]: p["id"]
                 for p in yt.playlists().list(part="snippet", mine=True, maxResults=50).execute().get("items", [])}
    wanted = []
    for n in PACK_NAMES:
        name = get_pack(n, s).display_name
        for fmt in ("Shorts", "Long Form"):
            t = f"{name} ({fmt})"
            if t in playlists:
                wanted.append((t, playlists[t]))
    existing = yt.channelSections().list(part="snippet,contentDetails", mine=True).execute().get("items", [])
    have = {tuple(sec.get("contentDetails", {}).get("playlists", [])) for sec in existing}
    pos = len(existing)
    for title, pid in wanted:
        if (pid,) in have:
            continue
        yt.channelSections().insert(part="snippet,contentDetails", body={
            "snippet": {"type": "singlePlaylist", "title": title, "position": pos},
            "contentDetails": {"playlists": [pid]}}).execute()
        pos += 1
        print(f"section added: {title}")
    print("done")


if __name__ == "__main__":
    main()
