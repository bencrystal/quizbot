"""Pull the Pokémon LP Wiki pronunciation guide (all generations) into assets/pokemon_pronunciations.json.

Source: https://pokemonlp.fandom.com/wiki/Pokémon_Pronunciation_Guide (per-generation subpages, read via the
MediaWiki API). The daily workflow re-runs this before every render so new names are picked up automatically;
if the wiki is unreachable the committed copy is used.

Output: {"<dex>": {"name": "Bulbasaur", "pron": "BULL-buh-sohr", "ipa": "/ˈbʊlbəsɔːɹ/"}, ...}
"""
from __future__ import annotations

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import requests

API = "https://pokemonlp.fandom.com/api.php"
GENS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX"]
OUT = Path(__file__).resolve().parent.parent / "assets" / "pokemon_pronunciations.json"


class _Rows(HTMLParser):
    """Collect table rows as lists of cell texts (no external deps)."""

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def fetch() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for g in GENS:
        r = requests.get(API, params={"action": "parse", "format": "json", "prop": "text",
                                      "page": f"Pokémon Pronunciation Guide/Generation {g}"},
                         headers={"User-Agent": "quizbot/1.0 (pronunciation guide sync)"}, timeout=60)
        r.raise_for_status()
        p = _Rows()
        p.feed(r.json()["parse"]["text"]["*"])
        n = 0
        for c in p.rows:
            # MS | Dex # | Pokémon | Type(s)... | Pronunciation | IPA   (type spans 1-2 cells)
            if len(c) < 5 or not re.match(r"^#\d+", c[1]):
                continue
            dex = int(c[1].lstrip("#"))
            out[str(dex)] = {"name": c[2], "pron": c[-2], "ipa": c[-1]}
            n += 1
        print(f"gen {g}: {n} names", file=sys.stderr)
    return out


if __name__ == "__main__":
    data = fetch()
    if len(data) < 1000:
        raise SystemExit(f"only {len(data)} names parsed; refusing to overwrite the committed guide")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=0) + "\n")
    print(f"wrote {len(data)} names → {OUT}")
