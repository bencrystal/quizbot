"""Quiz packs. A pack knows how to produce candidate QuizItems for a theme.

To add a new quiz type (bird calls, national anthems, movie scores, ...):
  1. Create quizbot/packs/<name>.py with a class subclassing Pack.
  2. Implement `themes()` and `candidates(theme)`.
  3. Register it in PACKS below.
Everything downstream (TTS, render, upload) is pack-agnostic.
"""
from __future__ import annotations

import random
from abc import ABC, abstractmethod
from pathlib import Path

from ..config import Settings
from ..models import QuizItem


class Pack(ABC):
    name: str = "base"
    display_name: str = "Audio Quiz"
    # Narration defaults a pack can override
    question_line: str = "Number {n}. What sound is this?"
    reveal_line: str = "It's {answer}!"
    channel_blurb: str = "Audio quizzes that teach you something."
    accent_color: tuple[int, int, int] = (255, 203, 5)
    bg_color: tuple[int, int, int] = (18, 18, 28)

    def __init__(self, settings: Settings):
        self.settings = settings
        self.cfg = settings.pack_config(self.name)
        self.cache = settings.cache_dir / self.name
        self.cache.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def themes(self) -> list[dict]:
        """Return possible themes: [{"id": "...", "label": "...", ...}]."""

    @abstractmethod
    def candidates(self, theme: dict) -> list[QuizItem]:
        """All items that fit a theme (may be large). Fetching/caching happens here."""

    def pick_theme(self, recent: list[str], rng: random.Random) -> dict:
        """Default: random theme not used recently. Planner may override via Claude."""
        options = [t for t in self.themes() if t["label"] not in recent] or self.themes()
        return rng.choice(options)

    def select(self, items: list[QuizItem], used: set[str], n: int, rng: random.Random) -> list[QuizItem]:
        fresh = [i for i in items if i.id not in used]
        if len(fresh) < n:
            fresh = items  # pool exhausted for this theme: allow repeats rather than fail
        rng.shuffle(fresh)
        return fresh[:n]


def get_pack(name: str, settings: Settings) -> Pack:
    from .pokemon import PokemonPack
    from .fixture import FixturePack

    PACKS = {p.name: p for p in (PokemonPack, FixturePack)}
    if name not in PACKS:
        raise SystemExit(f"Unknown pack '{name}'. Available: {', '.join(PACKS)}")
    return PACKS[name](settings)
