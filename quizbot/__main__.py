"""quizbot CLI.

  python -m quizbot run --format short                     one 9:16 Short (daily)
  python -m quizbot run --format long                      one 16:9 10-round episode (weekly)
  python -m quizbot run --pack pokemon --theme gen1 --mechanic choice --upload
  python -m quizbot test                                   offline smoke test, both formats, fixture pack
  python -m quizbot auth                                   one-time YouTube OAuth
  python -m quizbot status                                 list episodes and their state
  python -m quizbot themes --pack pokemon
  python -m quizbot retry-uploads                          upload any rendered-but-not-uploaded episodes
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
    if not args.force and state.episodes_today() >= settings.max_episodes_per_day:
        notify(settings, f"⏸ daily cap reached ({settings.max_episodes_per_day}); not rendering. Use --force to override.")
        return 0
    pack_name = args.pack
    if pack_name == "auto":
        rotation = list(settings.packs_enabled) or ["pokemon"]
        if settings.format == "long":
            import time as _time
            week_ago = _time.time() - 7 * 86400
            due = [n for n in rotation if state.count_episodes("long", n, week_ago) < settings.longs_per_pack_per_week]
            if not due:
                notify(settings, "⏸ every pack already has this week's long-form; nothing to render.")
                return 0
            pack_name = due[0]
        else:
            pack_name = rotation[state.count_episodes("short") % len(rotation)]
        print(f"[plan] pack: {pack_name} (auto)")
    pack = get_pack(pack_name, settings)
    exp_tags: list[str] = []
    if settings.format == "short":
        forced = str(settings.shorts_cold_open).lower()          # workflow input overrides the experiment
        from .experiments import assign
        exp_id, variant, exp_tags = assign(settings, state)
        if forced in ("1", "true", "yes", "0", "false", "no"):
            settings.shorts_cold_open = forced
        if exp_id:
            print(f"[plan] experiment {exp_id}: variant {variant}")
    ep = plan_episode(pack, settings, state, seed=args.seed, theme_id=args.theme)
    if ep.format == "short":
        mode = str(settings.shorts_cold_open).lower()
        cold = (state.count_episodes("short") % 2 == 0) if mode == "auto" else mode in ("1", "true", "yes")
        ep.meta["cold_open"] = cold
        ep.tags += ["coldopen" if cold else "announce", *exp_tags]
        ep.slug += "-cold" if cold else "-ann"
    print(f"[plan] {ep.slug}: {', '.join(i.answer for i in ep.items)}")
    state.upsert_episode(ep.slug, ep.pack, ep.title, ep.theme, "planned", meta={"manual": bool(args.force)})
    if str(settings.track_used).lower() in (ep.format, "all", "true"):
        state.mark_used(ep.pack, ep.format, [i.id for i in ep.items], ep.slug)
    try:
        result = Renderer(settings, pack, TTS(settings)).render(ep)
    except Exception:
        state.release(ep.pack, ep.slug)
        state.upsert_episode(ep.slug, ep.pack, ep.title, ep.theme, "failed", error=traceback.format_exc()[-2000:])
        notify(settings, f"❌ render failed for {ep.slug}:\n{traceback.format_exc()[-800:]}")
        return 1
    state.upsert_episode(ep.slug, ep.pack, ep.title, ep.theme, "rendered",
                         video_path=str(result.video), thumbnail_path=str(result.thumbnail))
    variant = (" · cold open" if ep.meta.get("cold_open") else " · announce first") if ep.format == "short" else ""
    msg = f"🎬 {ep.format} rendered: {ep.title}{variant}  ({result.duration_seconds:.0f}s)\n{result.video}"

    if args.upload or settings.upload:
        try:
            from .upload import upload
            vid = upload(settings, ep, result)
            state.upsert_episode(ep.slug, ep.pack, ep.title, ep.theme, "uploaded", youtube_id=vid)
            msg += f"\n📺 https://youtu.be/{vid} ({settings.youtube_privacy})"
            msg += crosspost_line(settings, ep, result.video, state)
            result.video.unlink(missing_ok=True)      # keep the cache small; episode.json + thumbnail stay
        except Exception:
            state.upsert_episode(ep.slug, ep.pack, ep.title, ep.theme, "rendered", error=traceback.format_exc()[-2000:])
            msg += f"\n❌ upload failed (will retry next run):\n{traceback.format_exc()[-800:]}"
            notify(settings, msg)
            return 1
    notify(settings, msg)
    return 0


def crosspost_line(settings: Settings, ep, video, state) -> str:
    """Instagram/Facebook after YouTube; never fails the run."""
    try:
        from .publish.meta import crosspost
        posted = crosspost(settings, ep, video, state)
    except Exception as e:
        print(f"[meta] cross-post error: {str(e)[:200]}")
        return ""
    return "".join(f"\n📱 {p}: {i}" for p, i in posted.items())


def cmd_retry_uploads(args, settings: Settings) -> int:
    import json
    from pathlib import Path
    from .models import Episode, QuizItem, RenderResult
    from .upload import upload

    state = State(settings.state_dir / "quizbot.db")
    rc = 0
    for row in state.episodes("rendered"):
        meta_path = Path(row["video_path"]).parent / "episode.json"
        if not meta_path.exists() or not Path(row["video_path"]).exists():
            continue
        m = json.loads(meta_path.read_text())
        ep = Episode(slug=m["slug"], pack=m["pack"], title=m["title"], description=m["description"],
                     tags=m["tags"], intro="", outro="", theme=m["theme"], format=m.get("format", "long"),
                     items=[QuizItem(id=i["id"], answer=i["answer"], prompt_audio=Path(), fact=i["fact"]) for i in m["items"]])
        res = RenderResult(video=Path(row["video_path"]), thumbnail=Path(row["thumbnail_path"]), duration_seconds=0)
        try:
            vid = upload(settings, ep, res)
            state.upsert_episode(ep.slug, ep.pack, ep.title, ep.theme, "uploaded", youtube_id=vid)
            notify(settings, f"📺 (retry) https://youtu.be/{vid} for {ep.slug}" + crosspost_line(settings, ep, res.video, state))
            res.video.unlink(missing_ok=True)
        except Exception:
            notify(settings, f"❌ retry upload failed for {ep.slug}: {traceback.format_exc()[-500:]}")
            rc = 1
    return rc


def cmd_status(args, settings: Settings) -> int:
    state = State(settings.state_dir / "quizbot.db")
    for r in state.episodes():
        yt = f"https://youtu.be/{r['youtube_id']}" if r["youtube_id"] else ""
        print(f"{r['status']:9} {r['slug']:60} {yt}")
    return 0


def cmd_themes(args, settings: Settings) -> int:
    for t in get_pack(args.pack, settings).themes():
        print(f"{t['id']:16} {t['label']}")
    return 0


def cmd_auth(args, settings: Settings) -> int:
    from .upload import authorize
    authorize(settings)
    return 0


def cmd_test(args, settings: Settings) -> int:
    settings.tts_provider, settings.planner, settings.upload = "dev", "template", False
    settings.stage_seconds = 2
    rc = 0
    for fmt, mech, rounds in (("short", "ladder", 1), ("long", "ladder", 2), ("long", "choice", 1)):
        settings.format, settings.mechanic, settings.rounds_per_episode = fmt, mech, rounds
        a = argparse.Namespace(pack="fixture", theme=None, seed=rounds, upload=False, force=True)
        rc |= cmd_run(a, settings)
    return rc


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="quizbot")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--pack", default="auto", help="pack name, or auto = rotate through settings.packs_enabled")
    r.add_argument("--format", choices=["long", "short"])
    r.add_argument("--mechanic", choices=["ladder", "choice", "cold"])
    r.add_argument("--theme", help="theme id (see `themes`); random if omitted")
    r.add_argument("--rounds", type=int)
    r.add_argument("--seed", type=int)
    r.add_argument("--upload", action="store_true")
    r.add_argument("--force", action="store_true", help="ignore the daily cap")
    sub.add_parser("test")
    sub.add_parser("auth")
    sub.add_parser("status")
    sub.add_parser("retry-uploads")
    th = sub.add_parser("themes")
    th.add_argument("--pack", default="pokemon")

    args = p.parse_args(argv)
    settings = Settings.load()
    if args.cmd == "run":
        if args.format:
            settings.format = args.format
        if args.mechanic:
            settings.mechanic = args.mechanic
        if args.rounds:
            settings.rounds_per_episode = args.rounds
    return {"run": cmd_run, "test": cmd_test, "auth": cmd_auth, "status": cmd_status, "themes": cmd_themes,
            "retry-uploads": cmd_retry_uploads}[args.cmd](args, settings)


if __name__ == "__main__":
    sys.exit(main())
