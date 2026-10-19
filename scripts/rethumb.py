"""Regenerate and re-upload thumbnails for videos already on the channel (rethumb.yml).

For every uploaded episode in the ledger with a cached `out/<slug>/episode.json`, rebuild the items from the
pack (so the reveal art is on disk), render the thumbnail with the current generator, and `thumbnails.set` it.
Long-form videos join the `thumb` experiment: arms alternate (sil/art) and the `exp:thumb=<arm>` tag is added
to the video so the nightly judge counts them. Idempotent: a video keeps its arm once tagged.

  python scripts/rethumb.py [--format long|short|all] [--dry-run]
Quota: thumbnails.set 50 + videos.update 50 per video.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from googleapiclient.discovery import build  # noqa: E402
from googleapiclient.http import MediaFileUpload  # noqa: E402

from quizbot.config import Settings  # noqa: E402
from quizbot.experiments import load_config, load_state  # noqa: E402
from quizbot.packs import get_pack  # noqa: E402
from quizbot.render import thumb_items  # noqa: E402
from quizbot.render.scene import Scene  # noqa: E402
from quizbot.state import State  # noqa: E402
from quizbot.upload import _creds  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", default="all", choices=["long", "short", "all"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    s = Settings.load()
    state = State(s.state_dir / "quizbot.db")
    yt = build("youtube", "v3", credentials=_creds(s, interactive=False))
    scene = Scene(s, s.channel_name)
    cfg, st = load_config(), load_state()
    thumb_ex = next((e for e in cfg["experiments"] if e["id"] == "thumb"), None)
    arms = list(thumb_ex["variants"]) if thumb_ex and "thumb" not in st.get("decided", {}) else []
    decided = st.get("decided", {}).get("thumb")
    packs: dict = {}
    done = skipped = 0
    summary: list[str] = []
    rows = state.conn.execute("SELECT slug, pack, theme, youtube_id FROM episodes WHERE youtube_id IS NOT NULL ORDER BY created_at").fetchall()
    n_long = 0
    for r in rows:
        slug, pack_name, vid = r["slug"], r["pack"], r["youtube_id"]
        ej = s.out_dir / slug / "episode.json"
        if ej.exists():
            ep = json.loads(ej.read_text())
        else:   # render cache gone: fall back to the ledger (theme + format from the slug); subjects = the
                # theme's best-known items, which is fine for `sil` and still on-theme for `art`
            ep = {"theme": r["theme"], "format": "short" if "-short-" in slug else "long", "items": []}
        fmt = ep.get("format", "long")
        if a.format != "all" and fmt != a.format:
            continue
        pack = packs.setdefault(pack_name, get_pack(pack_name, s))
        theme = next((t for t in pack.themes() if t["label"] == ep.get("theme")), None)
        if theme is None:
            print(f"skip {slug}: theme '{ep.get('theme')}' not in pack"); skipped += 1; continue
        want = [it["id"] for it in ep["items"]]
        pool = {it.id: it for it in pack.candidates(theme)}
        items = [pool[i] for i in want if i in pool]
        if not items and pool:
            famous = sorted([it for it in pool.values() if pack.famous(it)] or list(pool.values()), key=lambda i: i.difficulty)
            items = famous[: (1 if fmt == "short" else 10)]
            print(f"note {slug}: no cached items, using the theme's best-known ones for the thumbnail")
        if not items:
            print(f"skip {slug}: items not found in pack"); skipped += 1; continue
        # arm: keep an existing exp:thumb tag, else alternate; Shorts always use the baseline/decided style
        snippet = yt.videos().list(part="snippet", id=vid).execute()["items"][0]["snippet"]
        tags = snippet.get("tags") or []
        arm = next((t.split("=", 1)[1].rstrip("*") for t in tags if t.startswith("exp:thumb=")), None)
        if fmt == "long" and arms and arm is None:
            arm = arms[n_long % len(arms)]
            tags.append(f"exp:thumb={arm}")
        style = (arm if fmt == "long" and arm in (arms or [decided]) else None) or decided or s.thumb_style
        if fmt == "long":
            n_long += 1
        class _E:  # what thumb_items needs
            pass
        e = _E(); e.items = items
        out = s.out_dir / slug / "thumbnail.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        scene.thumbnail(thumb_items(e), ep.get("theme", ""), out, style=style, short=(fmt == "short"),
                        rounds=len(items), tint=items[0].tint)
        line = f"{'would set' if a.dry_run else 'set'} {vid} ({fmt}, {style}{', tagged' if 'exp:thumb=' + str(arm) in tags and fmt == 'long' else ''}): {slug}"
        print(line); summary.append(line)
        if a.dry_run:
            continue
        yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(out))).execute()
        if fmt == "long" and arm and snippet.get("tags") != tags:
            snippet["tags"] = tags
            yt.videos().update(part="snippet", body={"id": vid, "snippet": snippet}).execute()
        done += 1
    print("---- summary ----\n" + "\n".join(summary))
    print(f"rethumbed {done} video(s); skipped {skipped}")


if __name__ == "__main__":
    main()
