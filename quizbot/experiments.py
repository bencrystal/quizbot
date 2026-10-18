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


def active_experiment(cfg: dict, st: dict) -> dict | None:
    for ex in cfg.get("experiments", []):
        if ex["id"] not in st.get("decided", {}):
            return ex
    return None


def _apply(settings, overrides: dict) -> None:
    for k, v in overrides.items():
        if hasattr(settings, k):
            setattr(settings, k, v)


def assign(settings, state) -> tuple[str | None, str | None, list[str]]:
    """Baseline = every decided winner; then alternate the active experiment's variants per Short."""
    cfg, st = load_config(), load_state()
    tags: list[str] = []
    for ex in cfg.get("experiments", []):
        win = st.get("decided", {}).get(ex["id"])
        if win and win in ex["variants"]:
            _apply(settings, ex["variants"][win])
            tags.append(f"exp:{ex['id']}={win}*")       # * = decided winner, not under test
    ex = active_experiment(cfg, st)
    if not ex:
        return None, None, tags
    names = list(ex["variants"])
    variant = names[state.count_episodes("short") % len(names)]
    _apply(settings, ex["variants"][variant])
    tags.append(f"exp:{ex['id']}={variant}")
    return ex["id"], variant, tags


def judge(videos: list[dict]) -> dict:
    """videos: exported analytics rows with 'tags' (list), 'format', 'avp', 'views'. Decides at most one
    experiment per call (the active one) and advances. Returns the new state (also written to disk)."""
    cfg, st = load_config(), load_state()
    ex = active_experiment(cfg, st)
    if not ex:
        return st
    min_n, max_n, lead = cfg.get("min_n", 4), cfg.get("max_n", 8), cfg.get("min_lead", 0.10)
    rows = {}
    for name in ex["variants"]:
        vs = [v for v in videos if v.get("format") == "short" and f"exp:{ex['id']}={name}" in (v.get("tags") or [])
              and v.get("avp") is not None]
        rows[name] = {"n": len(vs), "avp": (sum(v["avp"] for v in vs) / len(vs)) if vs else None,
                      "views": (sum(v["views"] for v in vs) / len(vs)) if vs else None}
    st.setdefault("results", {})[ex["id"]] = rows
    ns = [r["n"] for r in rows.values()]
    if min(ns) < min_n:
        save_state(st)
        return st
    ranked = sorted(rows.items(), key=lambda kv: (kv[1]["avp"], kv[1]["views"]), reverse=True)
    best, second = ranked[0], ranked[1]
    rel = (best[1]["avp"] - second[1]["avp"]) / max(second[1]["avp"], 1e-9)
    decided = rel >= lead or min(ns) >= max_n
    if decided:
        st["decided"][ex["id"]] = best[0]
        st["log"].append({"when": dt.date.today().isoformat(), "experiment": ex["id"], "winner": best[0],
                          "rows": rows, "reason": f"lead {rel:.0%} on %-viewed" if rel >= lead else f"max_n reached; leader by %-viewed"})
        nxt = active_experiment(cfg, st)
        st["active"] = nxt["id"] if nxt else None
    save_state(st)
    return st
