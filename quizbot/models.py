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
    reveal_image: Optional[Path] = None   # artwork shown on reveal (silhouetted during the question)
    subtitle: str = ""           # small line under the answer, e.g. "#025 · Electric"
    hints: list[str] = field(default_factory=list)   # hint ladder, easiest last: ["It's an Electric type", "the Mouse Pokémon"]
    choices: list[str] = field(default_factory=list) # multiple-choice distractors (filled by planner when mechanic=choice)
    difficulty: float = 0.0      # lower = easier; planner orders rounds easy → hard
    silhouette: bool = True
    pronunciation: Optional[str] = None   # override how TTS should say `answer`
    meta: dict = field(default_factory=dict)


@dataclass
class Episode:
    """A fully planned video, before any audio/video is generated."""

    slug: str
    pack: str
    title: str
    description: str
    tags: list[str]
    intro: str
    outro: str
    items: list[QuizItem]
    theme: str = ""
    format: str = "long"         # long (16:9, many rounds) | short (9:16, 1 round)
    mechanic: str = "ladder"     # ladder | choice | cold
    stage_seconds: int = 4       # thinking time after the cry and after each hint
    banter: dict = field(default_factory=dict)   # optional per-round host lines from the planner, {round_n: "..."}
    meta: dict = field(default_factory=dict)


@dataclass
class RenderResult:
    video: Path
    thumbnail: Path
    duration_seconds: float
