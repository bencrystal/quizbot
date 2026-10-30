"""Quiz packs. A pack knows how to produce candidate QuizItems for a theme.

To add a new quiz type (bird calls, national anthems, movie scores, ...):
  1. Create quizbot/packs/<name>.py with a class subclassing Pack.
  2. Implement `themes()` and `candidates(theme)`.
  3. Register it in PACKS below.
Everything downstream (TTS, render, upload) is pack-agnostic.
"""
from __future__ import annotations

import random
import re
from abc import ABC, abstractmethod
from pathlib import Path

from ..config import Settings
from ..models import QuizItem


class Pack(ABC):
    name: str = "base"
    display_name: str = "Audio Quiz"
    former_names: tuple[str, ...] = ()   # earlier series names: playlists carrying one are renamed, not duplicated
    noun: str = "sound"                 # what a single item is called in titles: "Can you name this {noun}…"
    plural: str = ""                      # plural of noun when not noun+"s" (Pokémon)
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

    def pick_theme(self, recent: list[str], rng: random.Random, mechanic: str = "ladder") -> dict:
        """Random theme not used recently (weighted; rapid_only themes only for rapid-fire). Planner may override."""
        pool = [t for t in self.themes() if (mechanic == "rapid") or not t.get("rapid_only")]
        options = [t for t in pool if t["label"] not in recent] or pool
        weights = [int(t.get("weight", 1)) for t in options]
        return rng.choices(options, weights=weights, k=1)[0]

    def famous(self, item: QuizItem) -> bool:   # packs override to define their "well-known" subset
        return True

    def tier(self, item: QuizItem) -> str:
        """Coarse popularity bucket (icon | known | deep), tagged on every upload as `tier:<x>` so experiments can
        be judged with item fame balanced out: a Charmander Short and a Beldum Short are not the same test."""
        d = item.difficulty
        return "icon" if d <= 0 else "known" if d <= 2 else "deep"

    def select(self, items: list[QuizItem], used: set[str], n: int, rng: random.Random, famous_only=False,
               theme: dict | None = None) -> list[QuizItem]:
        if famous_only:
            items = [i for i in items if self.famous(i)] or items
        fresh = [i for i in items if i.id not in used]
        if len(fresh) < n:
            fresh = items  # pool exhausted for this theme: allow repeats rather than fail
        rng.shuffle(fresh)
        chosen = fresh[:n]
        chosen.sort(key=lambda i: i.difficulty)   # progressive difficulty, easy → hard
        return chosen

    def make_choices(self, item: QuizItem, pool: list[QuizItem], rng: random.Random, k: int = 3) -> list[str]:
        """Distractors for multiple-choice mode: other answers from the same theme."""
        others = [i.answer for i in pool if i.answer != item.answer]
        rng.shuffle(others)
        return others[:k]

    # Narration hooks a pack may override
    opening_line: str = "How fast can you name this sound?"
    punch_line: str = "Name this sound!"            # the two-word opener (experiment `open`, arm `punch`)
    spoken_replacements: dict = {}

    def spoken(self, text: str) -> str:
        """Respell proper nouns for the narrator (longest names first so 'Mew' never clobbers 'Mewtwo')."""
        for k in sorted(self.spoken_replacements, key=len, reverse=True):
            text = re.sub(rf"\b{re.escape(k)}\b", self.spoken_replacements[k], text)
        return text
    pronunciations: dict = {}          # name -> respelling, handed to the TTS as guidance (text stays as written)
    ipa: dict = {}                     # name -> IPA, for engines that accept phoneme tags (ElevenLabs)

    def with_phonemes(self, text: str) -> str:
        """Wrap known names in <phoneme alphabet="ipa"> tags (SSML subset ElevenLabs understands)."""
        for k, v in sorted(self.ipa.items(), key=lambda kv: -len(kv[0])):
            text = re.sub(rf"\b{re.escape(k)}\b", f'<phoneme alphabet="ipa" ph="{v}">{k}</phoneme>', text)
        return text

    def pronunciation_notes(self, text: str) -> list[tuple[str, str]]:
        """(name, respelling) for every name that appears in `text`, longest first."""
        return [(k, v) for k, v in sorted(self.pronunciations.items(), key=lambda kv: -len(kv[0]))
                if re.search(rf"\b{re.escape(k)}\b", text)]
    reactions: list[str] = []          # post-reveal host lines; empty = none
    choice_line: str = "Is it {choices}?"


PACK_NAMES = ["pokemon", "birds"]   # real packs (fixture is test-only); rotation order comes from settings.packs_enabled


def _registry() -> dict:
    from .pokemon import PokemonPack
    from .birds import BirdsPack
    from .fixture import FixturePack
    return {p.name: p for p in (PokemonPack, BirdsPack, FixturePack)}


def get_pack(name: str, settings: Settings) -> Pack:
    packs = _registry()
    if name not in packs:
        raise SystemExit(f"Unknown pack '{name}'. Available: {', '.join(packs)}")
    return packs[name](settings)
