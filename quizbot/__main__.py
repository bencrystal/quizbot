"""quizbot CLI.

  python -m quizbot run  --pack pokemon            plan → tts → render → (upload) → record
  python -m quizbot run  --pack pokemon --theme gen1 --upload
  python -m quizbot test                           offline smoke test with the fixture pack
  python -m quizbot auth                           one-time YouTube OAuth
  python -m quizbot status                         list episodes and their state
  python -m quizbot themes --pack pokemon          list available themes
  python -m quizbot retry-uploads                  upload any rendered-but-not-uploaded episodes
"""
from __future__ import annotations

import argparse
import sys
import traceback

from .config import Settings
from .notify import notify
from .packs import get_pack
from .planner import plan_episode
from .render import Renderer
from .state import State
from .tts import TTS


def cmd_run(args, settings: Settings) -> int:
    state = State(settings.state_dir / "quizbot.db")
    pack = get_pack(args.pack, settings)
    ep = plan_episode(pack, settings, state, seed=args.seed, theme_id=args.theme)
    print(f"[plan] {ep.slug}: {len(ep.items)} rounds → {', '.join(i.answer for i in ep.items)}")
    state.upsert_episode(ep.slug, ep.pack, ep.title, ep.theme, "planned")
    state.mark_used(ep.pack, [i.id for i in ep.items], ep.slug)
    try:
        result = Renderer(settings, pack, TTS(settings)).render(ep)
    except Exception:
        state.release(ep.pack, ep.slug)
        state.upsert_episode(ep.slug, ep.pack, ep.title, ep.theme, "failed", error=traceback.format_exc()[-2000:])
        notify(settings, f"❌ render failed for {ep.slug}:\n{traceback.format_exc()[-800:]}")
        return 1
    state.upsert_episode(ep.slug, ep.pack, ep.title, ep.theme, "rendered",
                         video_path=str(result.video), thumbnail_path=str(result.thumbnail))
    msg = f"🎬 rendered {ep.slug} ({result.duration_seconds:.0f}s) → {result.video}"

    if args.upload or settings.upload:
        try:
            from .upload import upload
            vid = upload(settings, ep, result)
            state.upsert_episode(ep.slug, ep.pack, ep.title, ep.theme, "uploaded", youtube_id=vid)
            msg += f"\n📺 https://youtu.be/{vid} ({settings.youtube_privacy}"
            msg += f", publishes in {settings.schedule_hours_ahead}h)" if settings.schedule_hours_ahead else ")"
        except Exception:
            state.upsert_episode(ep.slug, ep.pack, ep.title, ep.theme, "rendered", error=traceback.format_exc()[-2000:])
            msg += f"\n❌ upload failed (will retry next run):\n{traceback.format_exc()[-800:]}"
            notify(settings, msg)
            return 1
    notify(settings, msg)
    return 0


def cmd_retry_uploads(args, settings: Settings) -> int:
    import json
    from .models import Episode, QuizItem, RenderResult
    from .upload import upload
    from pathlib import Path

    state = State(settings.state_dir / "quizbot.db")
    rc = 0
    for row in state.episodes("rendered"):
        meta_path = Path(row["video_path"]).parent / "episode.json"
        if not meta_path.exists():
            continue
        m = json.loads(meta_path.read_text())
        ep = Episode(slug=m["slug"], pack=m["pack"], title=m["title"], description=m["description"],
                     tags=m["tags"], intro="", outro="", theme=m["theme"],
                     items=[QuizItem(id=i["id"], answer=i["answer"], prompt_audio=Path(), fact=i["fact"]) for i in m["items"]])
        res = RenderResult(video=Path(row["video_path"]), thumbnail=Path(row["thumbnail_path"]), duration_seconds=0)
        try:
            vid = upload(settings, ep, res)
            state.upsert_episode(ep.slug, ep.pack, ep.title, ep.theme, "uploaded", youtube_id=vid)
            notify(settings, f"📺 (retry) https://youtu.be/{vid} for {ep.slug}")
        except Exception:
            notify(settings, f"❌ retry upload failed for {ep.slug}: {traceback.format_exc()[-500:]}")
            rc = 1
    return rc


def cmd_status(args, settings: Settings) -> int:
    state = State(settings.state_dir / "quizbot.db")
    for r in state.episodes():
        yt = f"https://youtu.be/{r['youtube_id']}" if r["youtube_id"] else ""
        print(f"{r['status']:9} {r['slug']:50} {yt}")
    return 0


def cmd_themes(args, settings: Settings) -> int:
    for t in get_pack(args.pack, settings).themes():
        print(f"{t['id']:16} {t['label']}")
    return 0


def cmd_auth(args, settings: Settings) -> int:
    from .upload import authorize
    authorize(settings)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="quizbot")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--pack", default="pokemon")
    r.add_argument("--theme", help="theme id (see `themes`); random if omitted")
    r.add_argument("--seed", type=int)
    r.add_argument("--upload", action="store_true", help="upload after render (overrides settings)")

    t = sub.add_parser("test")
    sub.add_parser("auth")
    sub.add_parser("status")
    sub.add_parser("retry-uploads")
    th = sub.add_parser("themes")
    th.add_argument("--pack", default="pokemon")

    args = p.parse_args(argv)
    settings = Settings.load()
    if args.cmd == "test":
        settings.tts_provider = "dev"
        settings.planner = "template"
        settings.rounds_per_episode = 3
        settings.countdown_seconds = 3
        settings.upload = False
        args.pack, args.theme, args.seed, args.upload = "fixture", None, 1, False
        return cmd_run(args, settings)
    return {"run": cmd_run, "auth": cmd_auth, "status": cmd_status, "themes": cmd_themes,
            "retry-uploads": cmd_retry_uploads}[args.cmd](args, settings)


if __name__ == "__main__":
    sys.exit(main())
