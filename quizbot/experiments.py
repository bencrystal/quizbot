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
    stride = 1
    if ex:
        names = list(ex["variants"])
        variant = names[n % len(names)]
        _apply(settings, ex["variants"][variant])
        tags.append(f"exp:{ex['id']}={variant}")
        exp_id, stride = ex["id"], len(names)
    # parallel experiments cycle at a slower rate so every variant meets every sequential variant equally
    for pex in parallel_experiments(cfg, st, fmt):
        pnames = list(pex["variants"])
        pv = pnames[(n // stride) % len(pnames)]
        _apply(settings, pex["variants"][pv])
        tags.append(f"exp:{pex['id']}={pv}")
        stride *= len(pnames)
    return exp_id, variant, tags


def judge(videos: list[dict]) -> dict:
    """videos: exported analytics rows with 'tags' (list), 'format', 'avp', 'views'. Decides at most one
    experiment per call (the active one) and advances. Returns the new state (also written to disk)."""
    cfg, st = load_config(), load_state()
    todo = [e for e in [active_experiment(cfg, st, "short"), *parallel_experiments(cfg, st, "short"),
                        active_experiment(cfg, st, "long"), *parallel_experiments(cfg, st, "long")] if e]
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
        # Item fame is the biggest peripheral factor (Charmander vs Beldum), so when every arm has videos in the
        # same fame tiers, score each arm as the equal-weight mean over those tiers instead of the raw mean.
        tiers = None
        for name in names:
            have = {v.get("tier") for v in sel_by[name] if v.get("tier")}
            tiers = have if tiers is None else tiers & have
        if tiers:
            for name in names:
                means = [sum(v[metric] for v in sel_by[name] if v.get("tier") == t) / sum(1 for v in sel_by[name] if v.get("tier") == t) for t in sorted(tiers)]
                out[name]["score"] = sum(means) / len(means)
                out[name]["balanced_on"] = sorted(tiers)
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
    ready = [pk for pk, r in by_pack.items() if min(x["n"] for x in r.values()) >= min_n]
    if len(ready) < len(by_pack):                      # every pack that has started must reach min_n per arm
        return st
    rel = {name: 0.0 for name in names}
    for pk in ready:
        mean = sum(by_pack[pk][n]["score"] for n in names) / len(names)
        for name in names:
            rel[name] += (by_pack[pk][name]["score"] / mean if mean else 1.0) / len(ready)
    ranked = sorted(names, key=lambda n: (rel[n], rows[n]["views"] or 0), reverse=True)
    best, second = ranked[0], ranked[1]
    gap = (rel[best] - rel[second]) / max(rel[second], 1e-9)
    # a winner must also lead inside every pack (no pack-specific reversal), unless max_n has been reached
    agree = all(by_pack[pk][best]["score"] >= max(by_pack[pk][n]["score"] for n in names if n != best) for pk in ready)
    maxed = all(min(x["n"] for x in by_pack[pk].values()) >= max_n for pk in ready)
    decided = (gap >= lead and agree) or maxed
    for name in names:
        rows[name]["rel"] = round(rel[name], 3)
    if decided:
        st["decided"][ex["id"]] = best
        st["log"].append({"when": dt.date.today().isoformat(), "experiment": ex["id"], "winner": best, "rows": rows,
                          "reason": (f"lead {gap:.0%} on {metric}, pack-adjusted, leading in every pack ({', '.join(ready)})"
                                     if gap >= lead and agree else f"max_n reached in every pack; leader by pack-adjusted {metric}")})
        nxt = active_experiment(cfg, st, "short")
        st["active"] = nxt["id"] if nxt else None
    return st
