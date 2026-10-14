"""Core data model. Every quiz pack produces QuizItems; the renderer consumes an Episode."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class QuizItem:
    """One round of a quiz: play a sound, let the viewer guess, reveal, teach."""

    id: str                      # stable id, e.g. "pokemon:25" — used for dedup across episodes
    answer: str                  # what gets revealed, e.g. "Pikachu"
    prompt_audio: Path           # the sound the viewer has to identify
    fact: str                    # what the narrator reads after the reveal (Pokédex entry, trivia, ...)
    reveal_image: Optional[Path] = None   # artwork shown on reveal (silhouetted during the question if silhouette=True)
    subtitle: str = ""           # small line under the answer, e.g. "#025 · Electric"
    hint: str = ""               # optional spoken hint during the countdown
    silhouette: bool = True      # show the blacked-out reveal_image during the question
    pronunciation: Optional[str] = None   # override how TTS should say `answer`
    meta: dict = field(default_factory=dict)


@dataclass
class Episode:
    """A fully planned video, before any audio/video is generated."""

    slug: str                    # filesystem-safe id, e.g. "pokemon-gen1-water-2026-10-14"
    pack: str                    # pack name, e.g. "pokemon"
    title: str                   # YouTube title
    description: str             # YouTube description
    tags: list[str]
    intro: str                   # narrator's opening line(s)
    outro: str                   # narrator's closing line(s)
    items: list[QuizItem]
    theme: str = ""              # human-readable theme the planner chose
    countdown_seconds: int = 5
    meta: dict = field(default_factory=dict)


@dataclass
class RenderResult:
    video: Path
    thumbnail: Path
    duration_seconds: float
