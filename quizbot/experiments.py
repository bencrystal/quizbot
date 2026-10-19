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


def assign(settings, state, fmt: str = "short") -> tuple[str | None, str | None, list[str]]:
    """Baseline = every decided winner; then alternate the active experiment's variants per episode of `fmt`."""
    cfg, st = load_config(), load_state()
    tags: list[str] = []
    for ex in cfg.get("experiments", []):
        win = st.get("decided", {}).get(ex["id"])
        if win and win in ex["variants"] and ex.get("format", "short") == fmt:
            _apply(settings, ex["variants"][win])
            tags.append(f"exp:{ex['id']}={win}*")       # * = decided winner, not under test
    n = state.count_episodes(fmt)
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
    rows = {}
    for name in ex["variants"]:
        vs = [v for v in videos if v.get("format") == fmt and f"exp:{ex['id']}={name}" in (v.get("tags") or [])
              and v.get(metric) is not None]
        rows[name] = {"n": len(vs), "avp": (sum(v["avp"] for v in vs if v.get("avp") is not None) / max(1, sum(1 for v in vs if v.get("avp") is not None))) if vs else None,
                      "views": (sum(v["views"] for v in vs) / len(vs)) if vs else None,
                      "metric": metric, "score": (sum(v[metric] for v in vs) / len(vs)) if vs else None}
    st.setdefault("results", {})[ex["id"]] = rows
    ns = [r["n"] for r in rows.values()]
    if min(ns) < min_n:
        return st
    ranked = sorted(rows.items(), key=lambda kv: (kv[1]["score"], kv[1]["views"]), reverse=True)
    best, second = ranked[0], ranked[1]
    rel = (best[1]["score"] - second[1]["score"]) / max(second[1]["score"], 1e-9)
    decided = rel >= lead or min(ns) >= max_n
    if decided:
        st["decided"][ex["id"]] = best[0]
        st["log"].append({"when": dt.date.today().isoformat(), "experiment": ex["id"], "winner": best[0],
                          "rows": rows, "reason": f"lead {rel:.0%} on {metric}" if rel >= lead else f"max_n reached; leader by {metric}"})
        nxt = active_experiment(cfg, st, "short")
        st["active"] = nxt["id"] if nxt else None
    return st
