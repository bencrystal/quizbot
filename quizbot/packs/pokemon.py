"""Pokémon pack: cries + Pokédex entries from PokéAPI (https://pokeapi.co, free, no auth).

Everything is cached under state/cache/pokemon/ so the API is hit once per Pokémon, ever.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

from ..models import QuizItem
from . import Pack

API = "https://pokeapi.co/api/v2"

GENERATIONS = {
    1: (1, 151), 2: (152, 251), 3: (252, 386), 4: (387, 493), 5: (494, 649),
    6: (650, 721), 7: (722, 809), 8: (810, 905), 9: (906, 1025),
}
TYPES = ["normal", "fire", "water", "grass", "electric", "ice", "fighting", "poison", "ground",
         "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy"]

# TTS reads these better than the raw names
PRONUNCIATIONS = {
    "Pikachu": "Peeka-choo", "Charizard": "Char-ih-zard", "Gyarados": "Gyar-a-dose",
    "Arceus": "Ar-kee-us", "Rayquaza": "Ray-kwah-zah", "Lucario": "Loo-car-ee-oh",
    "Mewtwo": "Mew-two", "Snorlax": "Snore-lax", "Gengar": "Gen-gar", "Ditto": "Ditto",
}


class PokemonPack(Pack):
    name = "pokemon"
    display_name = "Guess That Pokémon"
    question_line = "Round {n}. Who's that Pokémon?"
    reveal_line = "It's {answer}!"
    hint_line = "Hint: {hint}."
    channel_blurb = "Can you name the Pokémon from its cry alone?"
    accent_color = (255, 203, 5)
    bg_color = (24, 28, 58)

    # ---- themes -------------------------------------------------------------
    def themes(self) -> list[dict]:
        gens = self.cfg.get("generations") or [1, 2, 3, 4]
        out = [{"id": f"gen{g}", "label": f"Generation {g}", "gen": g} for g in gens]
        for t in self.cfg.get("types") or ["fire", "water", "grass", "electric", "psychic", "ghost", "dragon"]:
            out.append({"id": f"type-{t}", "label": f"{t.title()}-type Pokémon", "type": t})
        out.append({"id": "starters", "label": "Starter Pokémon", "ids": STARTERS})
        out.append({"id": "legendaries", "label": "Legendary Pokémon", "ids": LEGENDARIES})
        return out

    # ---- data ---------------------------------------------------------------
    def candidates(self, theme: dict) -> list[QuizItem]:
        if "gen" in theme:
            lo, hi = GENERATIONS[theme["gen"]]
            ids = list(range(lo, hi + 1))
        elif "type" in theme:
            ids = self._ids_for_type(theme["type"])
        else:
            ids = theme["ids"]
        max_id = max(hi for _, hi in GENERATIONS.values())
        items = []
        for pid in ids:
            if pid > max_id:
                continue
            try:
                item = self._item(pid)
            except Exception as e:  # one bad Pokémon shouldn't kill the run
                print(f"  [pokemon] skip #{pid}: {e}")
                continue
            if item:
                items.append(item)
        return items

    def _get(self, url: str, cache_name: str) -> dict:
        p = self.cache / cache_name
        if p.exists():
            return json.loads(p.read_text())
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        p.write_text(r.text)
        return r.json()

    def _download(self, url: str, dest: Path) -> Path:
        if not dest.exists():
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            dest.write_bytes(r.content)
        return dest

    def _ids_for_type(self, t: str) -> list[int]:
        data = self._get(f"{API}/type/{t}", f"type-{t}.json")
        ids = []
        for entry in data["pokemon"]:
            m = re.search(r"/pokemon/(\d+)/?$", entry["pokemon"]["url"])
            if m:
                ids.append(int(m.group(1)))
        return sorted(ids)

    def _item(self, pid: int) -> QuizItem | None:
        poke = self._get(f"{API}/pokemon/{pid}", f"pokemon-{pid}.json")
        species = self._get(poke["species"]["url"], f"species-{pid}.json")

        cry_url = (poke.get("cries") or {}).get("latest") or (poke.get("cries") or {}).get("legacy")
        art_url = (poke["sprites"].get("other") or {}).get("official-artwork", {}).get("front_default")
        if not cry_url or not art_url:
            return None

        cry = self._download(cry_url, self.cache / f"cry-{pid}.ogg")
        art = self._download(art_url, self.cache / f"art-{pid}.png")

        name = poke["name"].replace("-", " ").title()
        # Prefer a longer, newer English flavor text; normalise the weird whitespace PokéAPI returns.
        entries = [e["flavor_text"] for e in species["flavor_text_entries"] if e["language"]["name"] == "en"]
        entries = [re.sub(r"\s+", " ", e.replace("\f", " ").replace("POKéMON", "Pokémon")).strip() for e in entries]
        fact = max(entries, key=len) if entries else f"{name} is a Pokémon."
        genus = next((g["genus"] for g in species["genera"] if g["language"]["name"] == "en"), "")
        types = " / ".join(t["type"]["name"].title() for t in poke["types"])

        type_names = [t["type"]["name"].title() for t in poke["types"]]
        type_hint = f"it's a {type_names[0]} type" if len(type_names) == 1 else f"it's a {type_names[0]} and {type_names[1]} type"
        gen = next(g for g, (lo, hi) in GENERATIONS.items() if lo <= pid <= hi)
        hints = [type_hint, f"it's known as the {genus}" if genus else f"it was introduced in Generation {gen}"]

        return QuizItem(
            id=f"pokemon:{pid}",
            answer=name,
            prompt_audio=cry,
            reveal_image=art,
            fact=f"{name}, the {genus}. {fact}" if genus else fact,
            subtitle=f"#{pid:03d}  ·  {types}",
            hints=hints,
            difficulty=self._difficulty(pid, gen, species),
            pronunciation=PRONUNCIATIONS.get(name),
            meta={"dex": pid, "types": types, "genus": genus, "gen": gen,
                  "height_m": poke["height"] / 10, "weight_kg": poke["weight"] / 10},
        )

    @staticmethod
    def _difficulty(pid: int, gen: int, species: dict) -> float:
        """Rough fame heuristic: starters/legendaries/mascots and early gens are easier."""
        d = float(gen)
        if pid in FAMOUS:
            d -= 3
        if species.get("is_legendary") or species.get("is_mythical"):
            d -= 1.5
        if species.get("is_baby"):
            d += 1
        return d


FAMOUS = {1, 3, 4, 6, 7, 9, 25, 26, 39, 52, 54, 94, 129, 130, 131, 133, 143, 149, 150, 151,
          152, 155, 158, 172, 175, 196, 197, 249, 250, 252, 255, 258, 282, 384, 445, 448, 658, 778}

STARTERS = [1, 4, 7, 152, 155, 158, 252, 255, 258, 387, 390, 393, 495, 498, 501,
            650, 653, 656, 722, 725, 728, 810, 813, 816, 906, 909, 912]
LEGENDARIES = [144, 145, 146, 150, 151, 243, 244, 245, 249, 250, 251, 377, 378, 379, 380, 381,
               382, 383, 384, 385, 386, 480, 481, 482, 483, 484, 485, 486, 487, 488, 491, 492, 493]
