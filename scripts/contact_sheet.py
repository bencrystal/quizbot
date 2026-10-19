"""Contact sheet of a rendered episode: the frames a viewer actually sees, tiled into one PNG with timestamps.

  python scripts/contact_sheet.py [video.mp4 ...] [-o out.png] [-n 12]

No argument: every .mp4 under out/ (the smoke test's renders). The supervisor looks at the sheet to catch
layout bugs (overlapping text, clipped names, unreadable captions) without being able to watch the video.
Frames are taken at scene changes first (hint arrivals, the reveal), then padded evenly if too few.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent


def duration(video: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
                         capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


def scene_times(video: Path, thresh: float = 0.25) -> list[float]:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video), "-vf", f"select='gt(scene,{thresh})',showinfo",
                        "-an", "-f", "null", "-"], capture_output=True, text=True)
    ts = []
    for line in r.stderr.splitlines():
        if "pts_time:" in line:
            ts.append(float(line.split("pts_time:")[1].split()[0]))
    return ts


def frame(video: Path, t: float, work: Path) -> Image.Image:
    p = work / f"f{t:08.2f}.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", str(p)], check=True)
    return Image.open(p).convert("RGB")


def sheet(video: Path, n: int, work: Path) -> Image.Image:
    d = duration(video)
    times = [0.3] + [t for t in scene_times(video) if 0.5 < t < d - 0.2]
    # thin clustered scene changes (animations fire several in a row), then pad evenly to n
    kept: list[float] = []
    for t in times:
        if not kept or t - kept[-1] > 0.8:
            kept.append(t)
    if len(kept) > n:
        step = len(kept) / n
        kept = [kept[int(i * step)] for i in range(n)]
    while len(kept) < n:
        gaps = sorted(zip([b - a for a, b in zip(kept, kept[1:] + [d])], kept), reverse=True)
        gap, start = gaps[0]
        kept.append(start + gap / 2)
        kept.sort()
    frames = [frame(video, t, work) for t in kept]
    w, h = frames[0].size
    scale = 420 / w if w > h else 300 / w          # landscape tiles 420 wide, portrait 300 wide
    tw, th = int(w * scale), int(h * scale)
    cols = 4 if w > h else 6
    rows = -(-len(frames) // cols)
    pad, cap = 10, 22
    im = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + pad + cap) + pad + 30), (24, 24, 26))
    dr = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    dr.text((pad, 8), f"{video.name}  ·  {d:.0f}s  ·  {len(frames)} frames", fill=(230, 230, 225), font=font)
    for i, (t, fr) in enumerate(zip(kept, frames)):
        x = pad + (i % cols) * (tw + pad)
        y = 30 + pad + (i // cols) * (th + pad + cap)
        im.paste(fr.resize((tw, th), Image.LANCZOS), (x, y))
        dr.text((x, y + th + 3), f"{t:5.1f}s", fill=(180, 180, 175), font=font)
    return im


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="*", type=Path)
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("-n", type=int, default=12)
    a = ap.parse_args()
    videos = a.videos or sorted((ROOT / "out").rglob("*.mp4"))
    if not videos:
        raise SystemExit("no videos found (run `python -m quizbot test` first)")
    work = ROOT / "out" / "_frames"
    work.mkdir(parents=True, exist_ok=True)
    for v in videos:
        out = a.out if (a.out and len(videos) == 1) else v.with_suffix(".sheet.png")
        sheet(v, a.n, work).save(out)
        print(out)


if __name__ == "__main__":
    main()
