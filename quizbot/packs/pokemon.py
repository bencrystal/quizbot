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

REGIONS = {1: "Kanto", 2: "Johto", 3: "Hoenn", 4: "Sinnoh", 5: "Unova", 6: "Kalos", 7: "Alola", 8: "Galar", 9: "Paldea"}

# Spoken respellings. "Pokémon"/"Pokédex" are fixed; every Pokémon name comes from the Pokémon LP Wiki
# pronunciation guide (assets/pokemon_pronunciations.json, refreshed by scripts/fetch_pronunciations.py before
# each CI run). Respellings are applied to every spoken line; on-screen text is never touched.
PRONUNCIATIONS = {"Pokémon": "Pokeymon", "Pokédex": "Pokeydex"}
GUIDE_PATH = Path(__file__).resolve().parents[2] / "assets" / "pokemon_pronunciations.json"

# Syllables the TTS reads as something else when they stand alone in a hyphenated respelling.
_SYLLABLE_FIX = {"us": "uss", "a": "uh", "i": "eye", "o": "oh", "e": "ee", "u": "oo", "y": "ee"}


def _respell(pron: str) -> str:
    """Guide format is 'BULL-buh-sohr' (caps = stressed). Strip notes like '(female)' and fix lone syllables."""
    pron = re.sub(r"\s*\(.*?\)", "", pron).strip()
    parts = []
    for word in pron.split():
        syl = []
        for s_ in word.split("-"):
            fix = _SYLLABLE_FIX.get(s_.lower())
            syl.append((fix.upper() if s_.isupper() else fix) if fix else s_)
        parts.append("-".join(syl))
    return " ".join(parts)


def load_guide() -> dict[int, str]:
    """dex number → respelling, or {} if the guide file is missing."""
    return {k: _respell(v["pron"]) for k, v in _raw_guide().items() if v.get("pron")}


def load_ipa() -> dict[int, str]:
    """dex number → IPA (General American), e.g. /ˈluːdʒiə/, for TTS engines that accept phonemes."""
    return {k: v["ipa"].strip("/") for k, v in _raw_guide().items() if v.get("ipa")}


def _raw_guide() -> dict[int, dict]:
    if not GUIDE_PATH.exists():
        return {}
    try:
        return {int(k): v for k, v in json.loads(GUIDE_PATH.read_text()).items()}
    except Exception:
        return {}


class PokemonPack(Pack):
    name = "pokemon"
    display_name = "Name That Pokémon"
    former_names = ("Guess That Pokémon",)
    noun = "Pokémon"
    question_line = "Round {n}."
    reveal_line = "It's {answer}."
    opening_line = "How fast can you name this Pokémon?"
    punch_line = "Name this Pokémon!"
    plural = "Pokémon"
    spoken_replacements = PRONUNCIATIONS

    def __init__(self, settings):
        super().__init__(settings)
        self.guide = load_guide()
        self.ipa_guide = load_ipa()
        self.spoken_replacements = dict(PRONUNCIATIONS)
        self.pronunciations = {"Pokémon": "POH-kee-mon", "Pokédex": "POH-kee-dex"}   # for instruction-mode TTS
        self.ipa = {"Pokémon": "ˈpoʊkiˌmɑn", "Pokédex": "ˈpoʊkiˌdɛks"}               # for phoneme-capable TTS
        if not self.guide:
            print("  [pokemon] pronunciation guide missing; names will be read as written")
    channel_blurb = "Can you name the Pokémon from its cry alone? Guess that Pokémon by ear."
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
        # rapid-fire only: a random mix (the default, half the time) and two curated, verifiable sets
        out.append({"id": "mix", "label": "Pokémon Mix", "ids": MIX, "rapid_only": True, "weight": 3, "curated": True})
        out.append({"id": "ash", "label": "Ash's Pokémon", "ids": ASH, "rapid_only": True, "curated": True})
        out.append({"id": "kanto-gyms", "label": "Kanto Gym Leaders' Pokémon", "ids": KANTO_GYMS, "rapid_only": True, "curated": True})
        return out

    # ---- data ---------------------------------------------------------------
    def famous(self, item: QuizItem) -> bool:
        return item.meta.get("gen", 9) <= 4 or item.meta.get("dex") in FAMOUS

    def select(self, items, used, n, rng, famous_only=False, theme=None):
        chosen = super().select(items, used, n, rng, famous_only, theme)
        # Type episodes: round 1 is always a Gen 1-3 Pokémon so longtime fans get a foothold; the rest can be any gen.
        if theme and "type" in theme and n > 1 and chosen and chosen[0].meta.get("gen", 9) > 3:
            classics = [i for i in items if i.meta.get("gen", 9) <= 3 and i not in chosen and i.id not in used]
            classics = classics or [i for i in items if i.meta.get("gen", 9) <= 3 and i not in chosen]
            if classics:
                chosen[-1] = rng.choice(classics)          # swap the last pick for a classic, then re-sort
                chosen.sort(key=lambda i: i.difficulty)
                if chosen[0].meta.get("gen", 9) > 3:        # make sure the classic actually opens
                    k = next(j for j, i in enumerate(chosen) if i.meta.get("gen", 9) <= 3)
                    chosen.insert(0, chosen.pop(k))
        return chosen

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
                item = self._item(pid, theme)
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

    def _item(self, pid: int, theme: dict | None = None) -> QuizItem | None:
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
        art_ = "an" if type_names[0][0].lower() in "aeiou" else "a"
        type_hint = (f"It's {art_} {type_names[0]} type." if len(type_names) == 1
                     else f"It's {art_} {type_names[0]} and {type_names[1]} type.")
        gen = next(g for g, (lo, hi) in GENERATIONS.items() if lo <= pid <= hi)
        # Pokédex entry as hint 2, with the name swapped for "this Pokémon". The API slug drops the punctuation
        # the entries keep ("mr-mime" vs "MR. MIME", "farfetchd" vs "Farfetch'd", "ho-oh" vs "HO-OH"), so match
        # the letters with anything non-alphanumeric allowed between them.
        n = r"[\W_]*".join(re.escape(c) for c in re.sub(r"[^a-z0-9]", "", poke["name"].lower()))
        redacted = re.sub(rf"\b(a|an|the)\s+{n}'s\b", "its", fact, flags=re.I)
        redacted = re.sub(rf"\b{n}'s\b", "its", redacted, flags=re.I)
        redacted = re.sub(rf"\b(a|an|the|this|that)\s+{n}\b", "this Pokémon", redacted, flags=re.I)
        redacted = re.sub(rf"\b{n}\b", "this Pokémon", redacted, flags=re.I)
        redacted = re.sub(r"(^|[.!?]\s+)(its|this Pokémon)", lambda m: m.group(1) + m.group(2)[0].upper() + m.group(2)[1:], redacted)
        region_hint = f"It was first seen in the {REGIONS[gen]} region."
        hints = [type_hint, region_hint, f"From the Pokédex: {redacted}"]
        visuals = {type_hint: {"kind": "types", "types": type_names}, region_hint: {"kind": "chip", "text": REGIONS[gen]}}
        theme = theme or {}
        if "gen" in theme:          # a Generation episode already tells you the region
            hints.remove(region_hint)
        elif "type" in theme:       # a Type episode already tells you (at least one) type
            hints.remove(type_hint)
        pron = self.guide.get(pid)
        if pron:
            self.spoken_replacements[name] = pron
            self.pronunciations[name] = pron
        if self.ipa_guide.get(pid):
            self.ipa[name] = self.ipa_guide[pid]

        return QuizItem(
            id=f"pokemon:{pid}",
            answer=name,
            prompt_audio=cry,
            reveal_image=art,
            fact=f"{name}, the {genus}." if genus else f"{name}.",
            subtitle=f"#{pid:03d}  ·  {types}",
            hints=hints,
            tint=TYPE_COLORS.get(type_names[0].lower()),
            difficulty=self._difficulty(pid, gen, species),
            pronunciation=pron,
            meta={"dex": pid, "types": types, "genus": genus, "gen": gen, "entry": fact, "visuals": visuals,
                  # dual types: the frame tints to the first type, the spoken-word highlight takes the second
                  "tint2": TYPE_COLORS.get(type_names[1].lower()) if len(type_names) > 1 else None,
                  "height_m": poke["height"] / 10, "weight_kg": poke["weight"] / 10},
        )

    def tier(self, item: QuizItem) -> str:
        """icon = the mascots everyone knows (FAMOUS set); known = other Gen 1-3; deep = the rest."""
        dex, gen = item.meta.get("dex"), item.meta.get("gen", 9)
        return "icon" if dex in FAMOUS else "known" if gen <= 3 else "deep"

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


TYPE_COLORS = {
    "normal": (168, 168, 120), "fire": (240, 128, 48), "water": (104, 144, 240), "grass": (120, 200, 80),
    "electric": (248, 208, 48), "ice": (152, 216, 216), "fighting": (192, 48, 40), "poison": (160, 64, 160),
    "ground": (224, 192, 104), "flying": (168, 144, 240), "psychic": (248, 88, 136), "bug": (168, 184, 32),
    "rock": (184, 160, 56), "ghost": (112, 88, 152), "dragon": (112, 56, 248), "dark": (112, 88, 72),
    "steel": (184, 184, 208), "fairy": (238, 153, 172),
}

FAMOUS = {1, 3, 4, 6, 7, 9, 25, 26, 39, 52, 54, 94, 129, 130, 131, 133, 143, 149, 150, 151,
          152, 155, 158, 172, 175, 196, 197, 249, 250, 252, 255, 258, 282, 384, 445, 448, 658, 778}

# Ash Ketchum's own Pokémon across the anime (caught or raised, National Dex numbers; evolutions as he had them)
ASH = [25, 1, 4, 6, 7, 12, 18, 99, 89, 128, 131, 143, 214, 153, 155, 156, 158, 164, 232, 254, 277, 341, 324, 362,
       392, 398, 389, 418, 472, 443, 501, 495, 498, 499, 521, 559, 542, 536, 525, 553, 658, 663, 701, 706, 715,
       722, 745, 727, 809, 804, 149, 94, 448, 865, 882, 133]
# Kanto gym leaders' signature Pokémon (Red/Blue/Yellow): Brock, Misty, Lt. Surge, Erika, Koga, Sabrina, Blaine, Giovanni
KANTO_GYMS = [95, 121, 26, 45, 110, 65, 59, 112]
# "Mix": a shuffle of the well-known ones from every generation — the rapid-fire default
MIX = sorted({1, 3, 4, 6, 7, 9, 25, 26, 39, 52, 54, 94, 129, 130, 131, 133, 143, 149, 150, 151, 152, 155, 158, 172, 175,
              196, 197, 249, 250, 252, 255, 258, 282, 384, 445, 448, 658, 778, 12, 35, 37, 58, 59, 63, 65, 66, 68, 74,
              79, 81, 92, 95, 104, 105, 106, 107, 113, 115, 116, 120, 121, 122, 123, 124, 125, 126, 127, 128, 132, 134,
              135, 136, 137, 139, 142, 144, 145, 146, 147, 148, 179, 181, 183, 185, 190, 202, 208, 212, 214, 227, 229,
              230, 243, 244, 245, 248, 254, 257, 260, 263, 280, 281, 286, 302, 303, 306, 319, 330, 334, 350, 359, 373,
              376, 380, 381, 382, 383, 386, 387, 390, 393, 392, 395, 398, 405, 407, 428, 429, 437, 443, 444, 446, 447,
              448, 461, 462, 466, 467, 468, 470, 471, 475, 477, 479, 483, 484, 487, 491, 492, 493})

STARTERS = [1, 4, 7, 152, 155, 158, 252, 255, 258, 387, 390, 393, 495, 498, 501,
            650, 653, 656, 722, 725, 728, 810, 813, 816, 906, 909, 912]
LEGENDARIES = [144, 145, 146, 150, 151, 243, 244, 245, 249, 250, 251, 377, 378, 379, 380, 381,
               382, 383, 384, 385, 386, 480, 481, 482, 483, 484, 485, 486, 487, 488, 491, 492, 493]
