"""Fixture pack: synthesized tones + generated art. No network. Used for `quizbot test` and CI."""
from __future__ import annotations

import subprocess

from PIL import Image, ImageDraw

from ..models import QuizItem
from . import Pack

CRITTERS = [
    ("Blorb", 220, "A round creature that hums at exactly 220 hertz when content."),
    ("Zippit", 440, "Zippit's call is a pure A4. Orchestras have accidentally tuned to it."),
    ("Grumbo", 110, "Grumbo lives underground and communicates in low, patient tones."),
    ("Fweep", 880, "The highest-pitched of the fixture critters. Dogs find it fascinating."),
    ("Murm", 330, "Murm only sings at dusk, and only when nobody is watching."),
    ("Tokk", 165, "Tokk's clicks were once mistaken for a metronome by a tired drummer."),
]


class FixturePack(Pack):
    name = "fixture"
    display_name = "Guess That Critter"
    question_line = "Number {n}. Which critter makes this sound?"
    accent_color = (120, 220, 160)
    bg_color = (20, 24, 30)

    def themes(self) -> list[dict]:
        return [{"id": "all", "label": "All fixture critters"}]

    def candidates(self, theme: dict) -> list[QuizItem]:
        items = []
        for i, (name, hz, fact) in enumerate(CRITTERS, 1):
            wav = self.cache / f"{name.lower()}.wav"
            if not wav.exists():
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                                "-i", f"sine=frequency={hz}:duration=1.2",
                                "-af", "afade=t=out:st=0.9:d=0.3", str(wav)], check=True)
            png = self.cache / f"{name.lower()}.png"
            if not png.exists():
                img = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                hue = (i * 57) % 255
                d.ellipse((60, 60, 540, 540), fill=(hue, 180, 255 - hue, 255))
                d.ellipse((200, 220, 260, 280), fill=(20, 20, 20, 255))
                d.ellipse((340, 220, 400, 280), fill=(20, 20, 20, 255))
                img.save(png)
            items.append(QuizItem(id=f"fixture:{i}", answer=name, prompt_audio=wav,
                                  reveal_image=png, fact=fact, subtitle=f"#{i:03d}  ·  {hz} Hz"))
        return items
