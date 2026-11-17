"""Format registry and the format bandit (config/formats.yaml).

A *format* is a repeatable kind of video (rapid-fire cries, Pokémon-or-medicine, slowed cries …). Rapid-fire
slots pick one by weight × fitness, exactly like themes inside a pack: fitness is the format's recent median
views over the slot's median (state/formats.json, nightly), clamped 0.4–2.5, 1.0 when there is no data yet.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def registry() -> dict:
    return yaml.safe_load((ROOT / "config" / "formats.yaml").read_text())["formats"]


def fitness() -> dict:
    try:
        return {k: float(v["fitness"]) for k, v in json.loads((ROOT / "state" / "formats.json").read_text()).items() if v.get("n", 0) >= 3}
    except Exception:
        return {}


def pick(slot: str, rng: random.Random) -> tuple[str, dict]:
    """Choose a format for a slot. Returns (id, spec)."""
    reg = {k: v for k, v in registry().items() if v["slot"] == slot}
    fit = fitness()
    ids = list(reg)
    weights = [float(reg[k].get("weight", 1)) * fit.get(k, 1.0) for k in ids]
    fid = rng.choices(ids, weights=weights, k=1)[0]
    return fid, reg[fid]


def identify(slot: str, pack: str, theme_id: str | None) -> tuple[str, dict]:
    """The format a forced run (explicit pack/theme) belongs to."""
    reg = registry()
    for k, v in reg.items():
        if v["slot"] == slot and v["pack"] == pack and v.get("theme") and v["theme"] == theme_id:
            return k, v
    for k, v in reg.items():
        if v["slot"] == slot and v["pack"] == pack and not v.get("theme"):
            return k, v
    return f"{pack}-{slot}", {"slot": slot, "pack": pack, "mech": "rapid" if slot == "rapid" else "ladder", "cue": "sound", "twist": "none", "answer": "open"}


def tags(fid: str, spec: dict) -> list[str]:
    return [f"fmt:{fid}", f"mech:{spec.get('mech', '?')}", f"cue:{spec.get('cue', '?')}", f"twist:{spec.get('twist', 'none')}"]


def pinned_themes(pack: str) -> set[str]:
    """Theme ids that are formats of their own (never drawn as an ordinary theme of the base format)."""
    return {v["theme"] for v in registry().values() if v["pack"] == pack and v.get("theme")}
