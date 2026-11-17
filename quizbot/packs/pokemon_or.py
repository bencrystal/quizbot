"""'Pokémon or …?' — a binary word quiz. The host reads a name; is it a Pokémon or a <other thing>?

One mechanic, many word lists: each theme pairs obscure Pokémon names with a curated list in
assets/wordlists/<list>.yaml (medicine first; dinosaurs, furniture, typefaces … are just more files). Items
carry meta["binary"]; the renderer's binary round shows the word, two chips, and reveals with the Pokémon's
artwork or a drawn icon for the other side. Runs in the rapid-fire slots (mechanic `rapid`).
"""
from __future__ import annotations

import random
from pathlib import Path

import yaml
from PIL import Image, ImageDraw

from ..models import QuizItem
from . import Pack

LISTS = Path(__file__).resolve().parents[2] / "assets" / "wordlists"
MAX_DEX = 1025


class PokemonOrPack(Pack):
    name = "pokemon_or"
    display_name = "Pokémon Or…?"
    noun = "name"
    channel_blurb = "A name appears. Is it a Pokémon, or something else entirely?"
    reveal_line = "{answer}!"

    def themes(self) -> list[dict]:
        out = []
        for f in sorted(LISTS.glob("*.yaml")):
            spec = yaml.safe_load(f.read_text())
            other = spec["label"]
            out.append({"id": f.stem, "label": f"Pokémon or {other}?", "list": f.stem, "rapid_only": True, "curated": True,
                        "binary": ["Pokémon", other],
                        "title": f"Pokémon or {other.lower()}? Only real fans get {{n}}/{{n}} #shorts",
                        "intro": f"Pokémon, or {other.lower()}? {{N}} names. Call every one!",
                        "outro": "How many did you get out of {n}?"})
        return out

    def candidates(self, theme: dict) -> list[QuizItem]:
        """A fresh sample each call: ~10 lesser-known Pokémon (fetched through the Pokémon pack, so artwork and
        types come along) and ~12 entries from the word list. Sampling keeps the PokéAPI calls per run small."""
        from .pokemon import FAMOUS, PokemonPack
        spec = yaml.safe_load((LISTS / f"{theme['list']}.yaml").read_text())
        other, tint = spec["label"], tuple(spec.get("tint") or (64, 200, 190))
        rng = random.Random()
        items: list[QuizItem] = []
        poke = PokemonPack(self.settings)
        # mostly later generations (their names are the ones that pass for a prescription), a few early ones as gimmes
        ids = [i for i in rng.sample(range(494, MAX_DEX + 1), 30) + rng.sample(range(1, 494), 8) if i not in FAMOUS]
        rng.shuffle(ids)
        for pid in ids:
            if sum(1 for i in items if i.meta["binary"]["is"] == 0) >= 10:
                break
            try:
                p = poke._item(pid, {"label": theme["label"]})
            except Exception as e:
                print(f"  [pokemon_or] skip #{pid}: {e}")
                continue
            if not p or " " in p.answer or "-" in p.answer:      # one clean word reads best
                continue
            types = " / ".join(p.meta.get("type_names") or [])
            items.append(QuizItem(
                id=f"pokemon_or:{theme['list']}:p{pid}", answer="Pokémon", prompt_audio=Path(), fact="",
                reveal_image=p.reveal_image, subtitle=f"{p.answer} · {types} type", tint=p.tint, shape_hint=False,
                pronunciation=None, difficulty=p.difficulty,
                meta={"binary": {"word": p.answer, "is": 0, "labels": theme["binary"], "say": p.pronunciation or p.answer},
                      "type_names": p.meta.get("type_names")}))
        icon = self._icon(theme["list"], tint)
        for e in rng.sample(spec["items"], min(12, len(spec["items"]))):
            items.append(QuizItem(
                id=f"pokemon_or:{theme['list']}:{e['name'].lower()}", answer=other, prompt_audio=Path(), fact="",
                reveal_image=icon, subtitle=f"{e['name']} · {e['what']}", tint=tint, shape_hint=False, difficulty=1.0,
                meta={"binary": {"word": e["name"], "is": 1, "labels": theme["binary"], "say": e["name"]}}))
        return items

    def select(self, items, used, n, rng, famous_only=False, theme=None):
        """A mix of both sides (never fewer than two of either), shuffled so the pattern can't be guessed."""
        fresh = [i for i in items if i.id not in used] or items
        a = [i for i in fresh if i.meta["binary"]["is"] == 0]
        b = [i for i in fresh if i.meta["binary"]["is"] == 1]
        rng.shuffle(a)
        rng.shuffle(b)
        k = max(2, min(n - 2, rng.choice([n // 2, n - n // 2])))
        chosen = a[:k] + b[:n - k]
        rng.shuffle(chosen)
        return chosen[:n]

    def tier(self, item: QuizItem) -> str:
        return "known"

    def _icon(self, kind: str, tint: tuple) -> Path:
        """A drawn icon for the non-Pokémon side (a capsule for medicine; a plain tag otherwise), RGBA."""
        out = self.cache / f"icon-{kind}.png"
        if out.exists():
            return out
        S = 640
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if kind == "medicine":
            w, h = 460, 190
            x0, y0 = (S - w) // 2, (S - h) // 2
            d.rounded_rectangle((x0, y0, x0 + w, y0 + h), radius=h // 2, fill=(245, 245, 250, 255))
            d.rounded_rectangle((x0, y0, x0 + w // 2 + h // 2, y0 + h), radius=h // 2, fill=(*tint, 255))
            d.rectangle((x0 + w // 2, y0, x0 + w // 2 + h // 2, y0 + h), fill=(245, 245, 250, 255))
            d.rounded_rectangle((x0 + 40, y0 + 34, x0 + w // 2 - 30, y0 + 62), radius=14, fill=(255, 255, 255, 110))
            img = img.rotate(35, resample=Image.BICUBIC)
        else:
            d.rounded_rectangle((90, 190, S - 90, S - 190), radius=60, fill=(*tint, 255))
            d.ellipse((130, 290, 190, 350), fill=(18, 18, 28, 255))
        img.save(out)
        return out
