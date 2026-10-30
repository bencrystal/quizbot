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
    display_name = "Name That Critter"
    noun = "critter"
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
                                  reveal_image=png, fact=fact, subtitle=f"#{i:03d}  ·  {hz} Hz",
                                  hints=[f"Its pitch is {'above' if hz >= 300 else 'below'} 300 hertz.",
                                         f"Its name starts with {name[0]}.",
                                         f"From the field guide: {fact.replace(name, 'this critter')}"],
                                  tint=((hz * 7) % 200 + 55, 120, 255 - (hz * 3) % 200),
                                  difficulty=abs(hz - 440) / 100,
                                  # exercise every hint visual the real packs use (map, size ladder, type pills)
                                  meta={"visuals": {f"Its pitch is {'above' if hz >= 300 else 'below'} 300 hertz.": {"kind": "map", "regions": "EW" if hz >= 300 else "C"},
                                                    f"Its name starts with {name[0]}.": {"kind": "size", "rank": i % 4} if i % 2 else {"kind": "types", "types": ["Fire", "Flying"]}}}))
        return items
