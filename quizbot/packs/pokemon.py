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

# Spoken respellings (anime pronunciations). Applied to every spoken line; on-screen text is never touched.
# Names not listed are read as written, which TTS gets right for most.
PRONUNCIATIONS = {
    "Pokémon": "Pokeymon", "Pokédex": "Pokeydex",
    "Arceus": "Ar-kee-uss", "Rayquaza": "Ray-kway-zuh", "Gyarados": "Gyar-uh-dose", "Lucario": "Loo-car-ee-oh",
    "Charizard": "Char-ih-zard", "Pikachu": "Pee-kuh-choo", "Raichu": "Rye-choo", "Mewtwo": "Mew-too",
    "Articuno": "Ar-tih-koo-noh", "Zapdos": "Zap-dose", "Moltres": "Mole-trace", "Dragonite": "Dragon-ite",
    "Dratini": "Druh-tee-nee", "Dragonair": "Dragon-air", "Kabutops": "Kuh-boo-tops", "Omanyte": "Oh-muh-nite",
    "Omastar": "Oh-muh-star", "Aerodactyl": "Air-oh-dak-til", "Ninetales": "Nine-tails", "Vulpix": "Vul-pix",
    "Jynx": "Jinks", "Electabuzz": "Uh-lek-tuh-buzz", "Magmar": "Mag-mar", "Pinsir": "Pin-sir",
    "Tauros": "Tore-ohs", "Lapras": "Lap-russ", "Ditto": "Dit-toe", "Eevee": "Ee-vee", "Vaporeon": "Vay-por-ee-onn",
    "Jolteon": "Jolt-ee-onn", "Flareon": "Flare-ee-onn", "Espeon": "Ess-pee-onn", "Umbreon": "Um-bree-onn",
    "Leafeon": "Leaf-ee-onn", "Glaceon": "Glay-see-onn", "Sylveon": "Sil-vee-onn", "Porygon": "Por-ee-gon",
    "Snorlax": "Snore-lax", "Gengar": "Gang-gar", "Haunter": "Haunt-er", "Alakazam": "Al-uh-kuh-zam",
    "Kadabra": "Kuh-dab-ruh", "Abra": "Ab-ruh", "Machamp": "Muh-champ", "Machoke": "Muh-choke",
    "Machop": "Muh-chop", "Geodude": "Gee-oh-dude", "Onix": "On-ix", "Steelix": "Steel-ix", "Scyther": "Sy-ther",
    "Scizor": "Siz-orr", "Kangaskhan": "Kang-gus-con", "Rhydon": "Rye-don", "Rhyhorn": "Rye-horn",
    "Chansey": "Chan-see", "Blissey": "Bliss-ee", "Tangela": "Tan-gel-uh", "Seadra": "See-druh",
    "Kingdra": "King-druh", "Staryu": "Star-you", "Starmie": "Star-mee", "Magikarp": "Madge-ih-karp",
    "Mew": "Mew", "Chikorita": "Chick-orr-ee-tuh", "Cyndaquil": "Sin-duh-kwill", "Typhlosion": "Tie-flo-zhun",
    "Totodile": "Toe-toe-dile", "Feraligatr": "Fer-al-ih-gator", "Hoothoot": "Hoot-hoot", "Xatu": "Zah-too",
    "Ampharos": "Am-fuh-ross", "Azumarill": "Az-you-muh-rill", "Sudowoodo": "Soo-doe-woo-doe",
    "Politoed": "Pol-ee-toad", "Wobbuffet": "Wob-buh-fet", "Heracross": "Hair-uh-cross", "Houndoom": "Hound-doom",
    "Tyranitar": "Tie-ran-ih-tar", "Lugia": "Loo-gee-uh", "Ho-Oh": "Ho-oh", "Celebi": "Sell-uh-bee",
    "Raikou": "Rye-koo", "Entei": "En-tay", "Suicune": "Swee-koon", "Treecko": "Treek-oh", "Sceptile": "Sep-tile",
    "Torchic": "Tor-chick", "Blaziken": "Blaze-ih-ken", "Mudkip": "Mud-kip", "Swampert": "Swamp-ert",
    "Gardevoir": "Gar-deh-vwar", "Aggron": "Ag-gron", "Milotic": "My-lo-tick", "Absol": "Ab-sol",
    "Salamence": "Sal-uh-mence", "Metagross": "Met-uh-gross", "Regice": "Rej-ice", "Regirock": "Rej-ee-rock",
    "Registeel": "Rej-ee-steel", "Latias": "Lat-ee-uss", "Latios": "Lat-ee-ose", "Kyogre": "Kai-oh-gur",
    "Groudon": "Grow-don", "Jirachi": "Jer-ah-chee", "Deoxys": "Dee-ox-iss", "Turtwig": "Tur-twig",
    "Chimchar": "Chim-char", "Infernape": "In-fer-nape", "Piplup": "Pip-lup", "Empoleon": "Em-pole-ee-onn",
    "Luxray": "Lux-ray", "Garchomp": "Gar-chomp", "Lopunny": "Lop-bunny", "Dialga": "Dee-al-guh",
    "Palkia": "Pal-kee-uh", "Giratina": "Gear-uh-tee-nuh", "Darkrai": "Dark-rye", "Uxie": "Yook-see",
    "Mesprit": "Mess-prit", "Azelf": "Ay-zelf", "Cresselia": "Cress-eel-ee-uh", "Heatran": "Heat-ran",
    "Regigigas": "Rej-ee-gig-uss", "Manaphy": "Man-uh-fee", "Shaymin": "Shay-min", "Greninja": "Gre-nin-ja",
    "Xerneas": "Zur-nee-uss", "Yveltal": "Ee-vel-tall", "Mimikyu": "Mim-ee-kyoo", "Incineroar": "In-sin-er-oar",
    "Zacian": "Zay-shan", "Zamazenta": "Zam-uh-zen-tuh", "Eternatus": "Ee-ter-nay-tus",
}


class PokemonPack(Pack):
    name = "pokemon"
    display_name = "Guess That Pokémon"
    noun = "Pokémon"
    question_line = "Round {n}."
    reveal_line = "It's {answer}."
    opening_line = "How fast can you name this Pokémon?"
    spoken_replacements = PRONUNCIATIONS
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
    def famous(self, item: QuizItem) -> bool:
        return item.meta.get("gen", 9) <= 4 or item.meta.get("dex") in FAMOUS

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
        type_hint = f"It's a {type_names[0]} type." if len(type_names) == 1 else f"It's a {type_names[0]} and {type_names[1]} type."
        gen = next(g for g, (lo, hi) in GENERATIONS.items() if lo <= pid <= hi)
        # Pokédex entry as hint 2, with the name swapped for "this Pokémon"
        n = re.escape(name)
        redacted = re.sub(rf"\b(a|an|the)\s+{n}'s\b", "its", fact, flags=re.I)
        redacted = re.sub(rf"\b{n}'s\b", "its", redacted, flags=re.I)
        redacted = re.sub(rf"\b(a|an|the)\s+{n}\b", "this Pokémon", redacted, flags=re.I)
        redacted = re.sub(rf"\b{n}\b", "this Pokémon", redacted, flags=re.I)
        redacted = re.sub(r"(^|[.!?]\s+)(its|this Pokémon)", lambda m: m.group(1) + m.group(2)[0].upper() + m.group(2)[1:], redacted)
        hints = [type_hint, f"It was first seen in the {REGIONS[gen]} region.", f"Last hint, from the Pokédex: {redacted}"]

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
            pronunciation=PRONUNCIATIONS.get(name),
            meta={"dex": pid, "types": types, "genus": genus, "gen": gen, "entry": fact,
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


TYPE_COLORS = {
    "normal": (168, 168, 120), "fire": (240, 128, 48), "water": (104, 144, 240), "grass": (120, 200, 80),
    "electric": (248, 208, 48), "ice": (152, 216, 216), "fighting": (192, 48, 40), "poison": (160, 64, 160),
    "ground": (224, 192, 104), "flying": (168, 144, 240), "psychic": (248, 88, 136), "bug": (168, 184, 32),
    "rock": (184, 160, 56), "ghost": (112, 88, 152), "dragon": (112, 56, 248), "dark": (112, 88, 72),
    "steel": (184, 184, 208), "fairy": (238, 153, 172),
}

FAMOUS = {1, 3, 4, 6, 7, 9, 25, 26, 39, 52, 54, 94, 129, 130, 131, 133, 143, 149, 150, 151,
          152, 155, 158, 172, 175, 196, 197, 249, 250, 252, 255, 258, 282, 384, 445, 448, 658, 778}

STARTERS = [1, 4, 7, 152, 155, 158, 252, 255, 258, 387, 390, 393, 495, 498, 501,
            650, 653, 656, 722, 725, 728, 810, 813, 816, 906, 909, 912]
LEGENDARIES = [144, 145, 146, 150, 151, 243, 244, 245, 249, 250, 251, 377, 378, 379, 380, 381,
               382, 383, 384, 385, 386, 480, 481, 482, 483, 484, 485, 486, 487, 488, 491, 492, 493]
