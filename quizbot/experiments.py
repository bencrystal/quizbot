"""Sequential A/B experiments for Shorts (config/experiments.yaml + state/experiments.json).

  assign(settings, state)  → applies decided winners + the active experiment's variant to `settings`,
                             returns (experiment_id, variant, tags) or (None, None, [])
  judge(videos)            → evaluates the active experiment from exported analytics; returns the updated
                             state dict (callers persist it)
state/experiments.json is committed to the repo (not the cache) so decisions survive anything:
  {"decided": {"open": "announce"}, "active": "title", "log": [...]}
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "experiments.yaml"
STATE = ROOT / "state" / "experiments.json"


def load_config() -> dict:
    return yaml.safe_load(CONFIG.read_text()) if CONFIG.exists() else {"experiments": []}


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"decided": {}, "active": None, "log": []}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1) + "\n")


def active_experiment(cfg: dict, st: dict, fmt: str = "short") -> dict | None:
    """The next undecided *sequential* experiment for this format (experiments default to Shorts)."""
    for ex in cfg.get("experiments", []):
        if ex["id"] not in st.get("decided", {}) and not ex.get("parallel") and ex.get("format", "short") == fmt:
            return ex
    return None


def parallel_experiments(cfg: dict, st: dict, fmt: str = "short") -> list[dict]:
    """Experiments flagged parallel run alongside the sequential one (e.g. the voice provider test)."""
    return [ex for ex in cfg.get("experiments", []) if ex.get("parallel") and ex["id"] not in st.get("decided", {})
            and ex.get("format", "short") == fmt]


def _enabled_packs() -> set[str]:
    """Packs in the current rotation (settings.packs_enabled); empty if settings cannot be read."""
    try:
        from .config import Settings
        return set(Settings.load().packs_enabled or [])
    except Exception:
        return set()


def _apply(settings, overrides: dict) -> None:
    for k, v in overrides.items():
        if hasattr(settings, k):
            setattr(settings, k, v)


def assign(settings, state, fmt: str = "short", pack: str | None = None) -> tuple[str | None, str | None, list[str]]:
    """Baseline = every decided winner; then alternate the active experiment's variants per episode of `fmt`.

    Alternation is counted *within the pack*: packs rotate per Short too, so counting across packs locked
    variant to pack (every Pokémon Short cold, every bird Short announce) and the first decision compared packs,
    not openings. Judging is stratified by pack for the same reason (see _judge_one)."""
    cfg, st = load_config(), load_state()
    tags: list[str] = []
    for ex in cfg.get("experiments", []):
        win = st.get("decided", {}).get(ex["id"])
        if win and win in ex["variants"] and ex.get("format", "short") == fmt:
            _apply(settings, ex["variants"][win])
            tags.append(f"exp:{ex['id']}={win}*")       # * = decided winner, not under test
    n = state.count_episodes(fmt, pack=pack)
    ex = active_experiment(cfg, st, fmt)
    exp_id = variant = None
    if ex:
        variant = _allocate(ex, st, n, pack)
        _apply(settings, ex["variants"][variant])
        tags.append(f"exp:{ex['id']}={variant}")
        exp_id = ex["id"]
    for pex in parallel_experiments(cfg, st, fmt):
        pv = _allocate(pex, st, n, pack)
        _apply(settings, pex["variants"][pv])
        tags.append(f"exp:{pex['id']}={pv}")
    return exp_id, variant, tags


EXPLORE = 0.30          # share of episodes dealt out evenly regardless of the standings (keeps every arm reaching min_n)
PRIOR_SD = 12.0         # spread of avg-% -viewed between arms, as a prior; views7d arms are scored on log views


def _allocate(ex: dict, st: dict, n: int, pack: str | None) -> str:
    """Thompson sampling over the arms (Ben, 2026-11-11: selection pressure while a test runs). Until every arm
    has three judged videos the arms alternate in balanced seeded blocks; after that, 70 % of episodes go to
    the arm most likely to be best (a draw from each arm's posterior, highest wins) and 30 % are dealt out
    evenly so a trailing arm still reaches min_n and the judge can rule. Seeded per experiment/pack/episode,
    so a re-run of the same episode makes the same choice."""
    import math
    import random as _random
    names = list(ex["variants"])
    rng = _random.Random(f"{ex['id']}:{pack}:{n}")
    res = (st.get("results") or {}).get(ex["id"]) or {}
    stats = {a: res.get(a) or {} for a in names}
    warm = all((stats[a].get("n") or 0) >= 3 and stats[a].get("score") is not None for a in names)
    if not warm or rng.random() < EXPLORE:
        block = _random.Random(f"{ex['id']}:{pack}:{n // len(names)}").sample(names, len(names))
        return block[n % len(names)]
    logscale = ex.get("metric") == "views7d"
    draws = {}
    for a in names:
        mu, k = float(stats[a]["score"]), int(stats[a]["n"])
        if logscale:
            mu, sd = math.log(max(mu, 1.0)), 0.9 / math.sqrt(k)
        else:
            sd = PRIOR_SD / math.sqrt(k)
        draws[a] = rng.gauss(mu, sd)
    return max(names, key=lambda a: draws[a])


def judge(videos: list[dict]) -> dict:
    """videos: exported analytics rows with 'tags' (list), 'format', 'avp', 'views'. Decides at most one
    experiment per call (the active one) and advances. Returns the new state (also written to disk)."""
    cfg, st = load_config(), load_state()
    todo = [e for f in ("short", "rapid", "long") for e in [active_experiment(cfg, st, f), *parallel_experiments(cfg, st, f)] if e]
    for ex in todo:
        st = _judge_one(cfg, st, ex, videos)
    save_state(st)
    return st


def _judge_one(cfg: dict, st: dict, ex: dict, videos: list[dict]) -> dict:
    min_n, max_n, lead = ex.get("min_n", cfg.get("min_n", 4)), ex.get("max_n", cfg.get("max_n", 8)), ex.get("min_lead", cfg.get("min_lead", 0.10))
    fmt, metric = ex.get("format", "short"), ex.get("metric", "avp")
    # metric: avp (average % viewed; the swipe-away signal) or views7d (views in the first 7 full days; the
    # click-through proxy — YouTube's API does not expose impressions/CTR). views7d only counts videos ≥7 d old.
    names = list(ex["variants"])

    def rows_for(vs: list[dict]) -> dict:
        out, sel_by = {}, {}
        for name in names:
            sel = [v for v in vs if f"exp:{ex['id']}={name}" in (v.get("tags") or []) and v.get(metric) is not None]
            sel_by[name] = sel
            out[name] = {"n": len(sel),
                         "avp": (sum(v["avp"] for v in sel if v.get("avp") is not None) / max(1, sum(1 for v in sel if v.get("avp") is not None))) if sel else None,
                         "views": (sum(v["views"] for v in sel) / len(sel)) if sel else None,
                         "subs": (sum(v.get("subs") or 0 for v in sel) / len(sel)) if sel else None,
                         "metric": metric, "score": (sum(v[metric] for v in sel) / len(sel)) if sel else None}
        # Peripheral factors are balanced out, not averaged over: item fame (Charmander vs Beldum) and, since the
        # voice test ran in parallel and Liam retained ~15 % better than the others, the voice. When every arm has
        # videos in the same (fame tier, voice) cells, an arm's score is the equal-weight mean over those cells;
        # if the cells don't line up, fall back to tier-only, then voice-only, then the raw mean.
        def cov(v, keys):
            voice = next((t.split("=", 1)[1] for t in (v.get("tags") or []) if t.startswith("exp:voice=")), None)
            parts = {"tier": v.get("tier"), "voice": voice}
            return tuple(parts[k] for k in keys) if all(parts[k] for k in keys) else None
        for keys in (("tier", "voice"), ("tier",), ("voice",)):
            cells = None
            for name in names:
                have = {cov(v, keys) for v in sel_by[name] if cov(v, keys)}
                cells = have if cells is None else cells & have
            if cells:
                for name in names:
                    means = [sum(v[metric] for v in sel_by[name] if cov(v, keys) == c) / sum(1 for v in sel_by[name] if cov(v, keys) == c)
                             for c in sorted(cells)]
                    out[name]["score"] = sum(means) / len(means)
                    out[name]["balanced_on"] = ["|".join(map(str, c)) for c in sorted(cells)]
                break
        return out

    pool = [v for v in videos if v.get("format") == fmt]
    rows = rows_for(pool)
    # Stratified by pack: packs differ hugely in baseline retention, so each pack is its own comparison and
    # the verdict is the average of the packs' relative scores (each pack's arms divided by that pack's mean).
    packs = sorted({v.get("pack") for v in pool if v.get("pack") and v.get("pack") != "unknown"})
    by_pack = {pk: rows_for([v for v in pool if v.get("pack") == pk]) for pk in packs}
    by_pack = {pk: r for pk, r in by_pack.items() if any(x["n"] for x in r.values())}
    for name in names:
        rows[name]["packs"] = {pk: {"n": r[name]["n"], "score": r[name]["score"]} for pk, r in by_pack.items()}
    st.setdefault("results", {})[ex["id"]] = rows
    if not by_pack:
        return st
    # A paused pack (out of settings.packs_enabled) can never grow its arms, so it is reported but never gates
    # the verdict: with birds paused on 2026-10-26 the `punch` arm had 0 bird Shorts, `open` could never reach
    # min_n "in every pack", and the whole sequential queue sat behind it.
    enabled = _enabled_packs()
    judged = {pk: r for pk, r in by_pack.items() if pk in enabled} or by_pack
    ready = [pk for pk, r in judged.items() if min(x["n"] for x in r.values()) >= min_n]
    if len(ready) < len(judged):                       # every live pack that has started must reach min_n per arm
        return st
    rel = {name: 0.0 for name in names}
    for pk in ready:
        mean = sum(by_pack[pk][n]["score"] for n in names) / len(names)
        for name in names:
            rel[name] += (by_pack[pk][name]["score"] / mean if mean else 1.0) / len(ready)
    ranked = sorted(names, key=lambda n: (rel[n], rows[n]["views"] or 0), reverse=True)
    best, second = ranked[0], ranked[1]
    gap = (rel[best] - rel[second]) / max(rel[second], 1e-9)
    # a retention winner must not be bleeding reach: if it trails the runner-up by >20 % on views, keep testing
    # (Shorts distribution is driven by swipe-away/completion, but a shorter video can win % viewed while
    # reaching fewer people — Ben, 2026-11-11)
    if metric == "avp" and (rows[best]["views"] or 0) < 0.8 * (rows[second]["views"] or 0) and not all(
            min(x["n"] for x in by_pack[pk].values()) >= max_n for pk in ready):
        return st
    # a winner must also lead inside every pack (no pack-specific reversal), unless max_n has been reached
    agree = all(by_pack[pk][best]["score"] >= max(by_pack[pk][n]["score"] for n in names if n != best) for pk in ready)
    maxed = all(min(x["n"] for x in by_pack[pk].values()) >= max_n for pk in ready)
    clear = gap >= lead and agree
    bleeding = metric == "avp" and (rows[best]["views"] or 0) < 0.8 * (rows[second]["views"] or 0)
    decided = clear or maxed
    for name in names:
        rows[name]["rel"] = round(rel[name], 3)
    if decided:
        # At max_n without a clear lead (or with the leader trailing on reach) the honest result is a tie, and a
        # tie keeps the incumbent — the first-listed variant, which is the baseline the test started from — rather
        # than crowning a 3 % leader (captions, 2026-11-13: static "won" 68.6 vs 66.9 while reaching 34 % fewer).
        winner = best if (clear and not bleeding) or (maxed and not bleeding and gap >= lead / 2) else names[0]
        st["decided"][ex["id"]] = winner
        st["log"].append({"when": dt.date.today().isoformat(), "experiment": ex["id"], "winner": winner, "rows": rows,
                          "reason": (f"lead {gap:.0%} on {metric}, pack-adjusted, leading in every pack ({', '.join(ready)})"
                                     if winner == best and clear else
                                     f"max_n reached; {best} leads by {gap:.0%} on {metric}" + (", trailing on views" if bleeding else "")
                                     + (f" — kept the incumbent {winner}" if winner != best else " — close enough to call"))})
        nxt = active_experiment(cfg, st, "short")
        st["active"] = nxt["id"] if nxt else None
    return st
