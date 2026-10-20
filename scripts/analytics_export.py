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
                "avd": None, "avp": None, "minutes": None, "subs": None, "tags": [t for t in tags if t.startswith("exp:")]
                        # the first day's Shorts predate exp: tags; they belong to the 'open' experiment
                        + ([f"exp:open={'cold' if variant == 'coldopen' else 'announce'}"] if variant and not any(t.startswith("exp:open=") for t in tags) else []),
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

    # ---- views in the first 7 full days per video (thumbnail/CTR proxy); only once the window has closed
    for v in videos.values():
        v["views7d"] = None
        pub = dt.date.fromisoformat(v["published"][:10])
        if (dt.date.today() - pub).days < 8:
            continue
        try:
            rep = yta.reports().query(ids="channel==MINE", startDate=pub.isoformat(), endDate=(pub + dt.timedelta(days=6)).isoformat(),
                                      metrics="views", filters=f"video=={v['id']}").execute()
            v["views7d"] = int((rep.get("rows") or [[0]])[0][0])
        except Exception as e:
            print(f"views7d skipped for {v['id']}: {str(e)[:80]}")

    # ---- channel by day
    rep = yta.reports().query(ids="channel==MINE", startDate=START, endDate=today, dimensions="day",
                              metrics="views,estimatedMinutesWatched,subscribersGained,subscribersLost", sort="day").execute()
    cols = [c["name"] for c in rep.get("columnHeaders", [])]
    daily = [dict(zip(cols, r)) for r in rep.get("rows", [])]

    # ---- ledger (restored from the Actions cache when present)
    ledger = {"episodes": [], "used": {}}
    db = s.state_dir / "quizbot.db"
    c = None
    if db.exists():
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        ledger["episodes"] = [dict(r) for r in c.execute(
            "SELECT slug, pack, title, theme, status, youtube_id, created_at FROM episodes ORDER BY created_at DESC LIMIT 400")]
        for r in c.execute("SELECT pack, fmt, COUNT(*) n FROM used_by_format GROUP BY pack, fmt"):
            ledger["used"].setdefault(r["pack"], {})[r["fmt"]] = r["n"]

    # ---- audience + traffic (Analytics API): who watches and how they arrive. Each query is independent.
    audience: dict[str, list] = {}
    for key, dims, metrics, sort in [
        ("countries", "country", "views,estimatedMinutesWatched", "-views"),
        ("ages", "ageGroup,gender", "viewerPercentage", "-viewerPercentage"),
        ("traffic", "insightTrafficSourceType", "views,estimatedMinutesWatched", "-views"),
        ("devices", "deviceType", "views", "-views"),
    ]:
        try:
            rep = yta.reports().query(ids="channel==MINE", startDate=START, endDate=today, dimensions=dims,
                                      metrics=metrics, sort=sort, maxResults=25).execute()
            cols = [c["name"] for c in rep.get("columnHeaders", [])]
            audience[key] = [dict(zip(cols, r)) for r in rep.get("rows", [])]
        except Exception as e:
            print(f"audience/{key} skipped: {str(e)[:100]}")
            audience[key] = []

    # ---- monetisation progress (YPP: 1,000 subs AND 4,000 public watch-hours on long-form in 365 d, or 10M
    #      Shorts views in 90 d). Shorts watch time does not count toward the 4,000 hours.
    def _window(ids_: list[str], days: int, metric: str) -> float:
        tot = 0.0
        for k in range(0, len(ids_), 200):
            try:
                rep = yta.reports().query(ids="channel==MINE", startDate=(dt.date.today() - dt.timedelta(days=days)).isoformat(),
                                          endDate=today, metrics=metric, filters="video==" + ",".join(ids_[k:k + 200])).execute()
                tot += float((rep.get("rows") or [[0]])[0][0])
            except Exception as e:
                print(f"ypp window skipped: {str(e)[:100]}")
        return tot
    long_ids = [v["id"] for v in videos.values() if v["format"] == "long"]
    short_ids = [v["id"] for v in videos.values() if v["format"] == "short"]
    ypp = {"subscribers": int(ch["statistics"].get("subscriberCount", 0)), "subscribers_needed": 1000,
           "watch_hours_365d": round(_window(long_ids, 365, "estimatedMinutesWatched") / 60, 1) if long_ids else 0.0,
           "watch_hours_needed": 4000,
           "shorts_views_90d": int(_window(short_ids, 90, "views")) if short_ids else 0, "shorts_views_needed": 10_000_000}
    # YouTube Analytics lags the public counters by 2-3 days (and longer for a new channel); until it has rows, use
    # the Data API view counts so the dashboard never shows 0 for a channel that plainly has views
    ypp["analytics_live"] = bool(daily)
    ypp["analytics_through"] = daily[-1]["day"] if daily else None      # Analytics runs 2-3 days behind the public counters
    # While the channel is younger than the 90-day window, the public lifetime counts are the better estimate of
    # the window (Analytics only has the days it has processed so far); after that the window itself is right.
    first_pub = min((v["published"][:10] for v in videos.values()), default=today)
    if (dt.date.today() - dt.date.fromisoformat(first_pub)).days < 90:
        ypp["shorts_views_90d"] = max(ypp["shorts_views_90d"], sum(v["views"] for v in videos.values() if v["format"] == "short"))

    # ---- budget: paid TTS credits (ElevenLabs), so a dry account is visible before a Short falls back
    budget: dict[str, dict] = {}
    el_key = __import__("os").environ.get("ELEVENLABS_API_KEY")
    if el_key:
        try:
            import requests as _rq
            sub = _rq.get("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": el_key}, timeout=30).json()
            if "detail" in sub and "tier" not in sub:          # key lacks the user_read permission, or invalid
                raise RuntimeError(str(sub["detail"])[:120])
            budget["elevenlabs"] = {"tier": sub.get("tier"), "used": sub.get("character_count"), "limit": sub.get("character_limit"),
                                    "resets": dt.datetime.fromtimestamp(sub["next_character_count_reset_unix"], dt.timezone.utc).date().isoformat()
                                    if sub.get("next_character_count_reset_unix") else None}
        except Exception as e:
            budget["elevenlabs"] = {"error": str(e)[:120]}

    # ---- cross-post metrics (Instagram / Facebook) joined to videos by episode slug
    platforms: dict[str, dict] = {}
    platform_summary: dict[str, dict] = {}
    try:
        from quizbot.publish.meta import token as meta_token, GRAPH
        import requests as _rq
        tok = meta_token(s)
        if tok and db.exists():
            slug_by_yt = {r["youtube_id"]: r["slug"] for r in c.execute("SELECT slug, youtube_id FROM episodes WHERE youtube_id IS NOT NULL")}
            for r in c.execute("SELECT episode_slug, platform, post_id FROM posts WHERE post_id IS NOT NULL"):
                slug, plat, pid = r["episode_slug"], r["platform"], r["post_id"]
                try:
                    if plat == "instagram":
                        d = _rq.get(f"{GRAPH}/{pid}/insights", params={
                            "metric": "views,reach,likes,comments,shares,saved,total_interactions,ig_reels_avg_watch_time",
                            "access_token": tok["page_token"]}, timeout=30).json()
                        m = {x["name"]: (x.get("values") or [{}])[0].get("value") for x in d.get("data", [])}
                    else:
                        d = _rq.get(f"{GRAPH}/{pid}/video_insights", params={
                            "metric": "total_video_views,total_video_avg_time_watched,post_impressions_unique,"
                                      "blue_reels_play_count,post_video_avg_time_watched",
                            "access_token": tok["page_token"]}, timeout=30).json()
                        m = {x["name"]: (x.get("values") or [{}])[0].get("value") for x in d.get("data", [])}
                        if "error" in d:
                            m = {"error": d["error"].get("message", "")[:120]}
                    platforms.setdefault(slug, {})[plat] = {"id": pid, **m}
                except Exception as e:
                    platforms.setdefault(slug, {})[plat] = {"id": pid, "error": str(e)[:120]}
            for v in videos.values():
                sl = slug_by_yt.get(v["id"])
                if sl and sl in platforms:
                    v["platforms"] = platforms[sl]
            # account level: followers + totals across every post we made, per platform (TikTok joins here later)
            def _sum(plat: str, *keys: str) -> int:
                return sum(int(next((m[k] for k in keys if isinstance(m.get(k), (int, float))), 0))
                           for p in platforms.values() for pl, m in p.items() if pl == plat)
            ig_acct = _rq.get(f"{GRAPH}/{tok['ig_user_id']}", params={"fields": "username,followers_count,media_count",
                                                                       "access_token": tok["page_token"]}, timeout=30).json() if tok.get("ig_user_id") else {}
            fb_acct = _rq.get(f"{GRAPH}/{tok['page_id']}", params={"fields": "name,followers_count,fan_count",
                                                                    "access_token": tok["page_token"]}, timeout=30).json()
            platform_summary["instagram"] = {"handle": ig_acct.get("username"), "followers": ig_acct.get("followers_count"),
                                             "posts": sum(1 for p in platforms.values() if "instagram" in p),
                                             "views": _sum("instagram", "views"), "reach": _sum("instagram", "reach"),
                                             "likes": _sum("instagram", "likes"), "comments": _sum("instagram", "comments"),
                                             "shares": _sum("instagram", "shares"), "saves": _sum("instagram", "saved")}
            platform_summary["facebook"] = {"handle": fb_acct.get("name"), "followers": fb_acct.get("followers_count") or fb_acct.get("fan_count"),
                                            "posts": sum(1 for p in platforms.values() if "facebook" in p),
                                            "views": _sum("facebook", "blue_reels_play_count", "total_video_views"),
                                            "reach": _sum("facebook", "post_impressions_unique")}
    except Exception as e:
        print(f"cross-post metrics skipped: {str(e)[:120]}")
    platform_summary["youtube"] = {"handle": ch["snippet"]["title"], "followers": int(ch["statistics"].get("subscriberCount", 0)),
                                   "posts": len(videos),      # channel statistics lag a day or two behind per-video counts
                                   "views": max(int(ch["statistics"].get("viewCount", 0)), sum(v["views"] for v in videos.values())),
                                   "likes": sum(v["likes"] for v in videos.values()), "comments": sum(v["comments"] for v in videos.values())}

    # ---- recent comments (newest 25 public videos, up to 5 each): what viewers actually say
    comments = []
    api_key = __import__("os").environ.get("YT_API_KEY")   # commentThreads needs an API key or the force-ssl scope
    ytc = build("youtube", "v3", developerKey=api_key) if api_key else None
    for v in sorted(videos.values(), key=lambda v: v["published"], reverse=True)[:25] if ytc else []:
        if v["privacy"] != "public":
            continue
        try:
            r = ytc.commentThreads().list(part="snippet", videoId=v["id"], maxResults=5, order="relevance",
                                         textFormat="plainText").execute()
        except Exception as e:                      # comments disabled etc.
            print(f"comments skipped for {v['id']}: {str(e)[:80]}")
            continue
        for t in r.get("items", []):
            c = t["snippet"]["topLevelComment"]["snippet"]
            comments.append({"video": v["id"], "title": v["title"], "author": c.get("authorDisplayName", ""),
                             "text": c.get("textDisplay", "")[:300], "likes": c.get("likeCount", 0),
                             "at": c.get("publishedAt", "")})
    comments.sort(key=lambda c: c["at"], reverse=True)

    # ---- experiments: judge the active one from retention data, persist the decision
    from quizbot.experiments import judge, load_config
    exp_state = judge(list(videos.values()))
    exp_cfg = load_config()

    out = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "channel": {"title": ch["snippet"]["title"], "subscribers": int(ch["statistics"].get("subscriberCount", 0)),
                    "views": int(ch["statistics"].get("viewCount", 0)), "videos": int(ch["statistics"].get("videoCount", 0)),
                    "daily": daily},
        "packs": {n: {"display_name": p.display_name, "noun": p.noun} for n, p in packs.items()},
        "videos": sorted(videos.values(), key=lambda v: v["published"], reverse=True),
        "ledger": ledger,
        "platforms": platforms,
        "platform_summary": platform_summary,
        "audience": audience,
        "ypp": ypp,
        "budget": budget,
        "comments": comments[:60],
        "experiments": {"config": exp_cfg, "state": exp_state},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=0) + "\n")
    print(f"wrote {OUT}: {len(videos)} videos, {len(daily)} days")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:              # HttpError bodies carry the real reason (API not enabled, scope missing)
        print("ERROR:", getattr(e, "content", b"")[:1500].decode(errors="replace") if hasattr(e, "content") else e)
        raise

# Schema
# generated: ISO time
# channel: {title, subscribers, views, videos, daily: [{day, views, estimatedMinutesWatched, subscribersGained, subscribersLost}]}
# packs: {name: {display_name, noun}}
# videos: [{id, title, published, pack, format(short|long), variant(coldopen|announce|null), theme, duration(s),
#           privacy, views, likes, comments, avd(avg view duration s), avp(avg % viewed), minutes, subs,
#           views7d (views in the first 7 full days; null until the window closes), tags: [exp:...]}]
# ledger: {episodes: [{slug, pack, title, theme, status, youtube_id, created_at}], used: {pack: {short: n, long: n}}}
# platforms: {slug: {instagram: {id, views, reach, likes, ...}, facebook: {id, ...}}}  (also on each video as .platforms)
# platform_summary: {youtube|instagram|facebook: {handle, followers, posts, views, reach?, likes?, ...}}
# audience: {countries: [{country, views, estimatedMinutesWatched}], ages: [{ageGroup, gender, viewerPercentage}],
#            traffic: [{insightTrafficSourceType, views, ...}], devices: [{deviceType, views}]}
# ypp: {subscribers, subscribers_needed, watch_hours_365d, watch_hours_needed, shorts_views_90d, shorts_views_needed}
# budget: {elevenlabs: {tier, used, limit, resets}}
# experiments: {config: experiments.yaml, state: experiments.json (decided, active, results, log)}
