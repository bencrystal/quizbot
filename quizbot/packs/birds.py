"""Bird-call pack: xeno-canto recordings (CC-BY / CC-BY-SA only) + Wikipedia photos and descriptions.

Needs XC_API_KEY (free: xeno-canto.org → account page). Everything is cached under state/cache/birds/.
Species are a curated list of common US birds with region / family / size metadata for the hint ladder:
  region & habitat → family & size → field-guide line with the name redacted → reveal (photo, no silhouette).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import requests
from PIL import Image, ImageDraw

from ..models import QuizItem
from . import Pack

XC_API = "https://xeno-canto.org/api/3/recordings"
WIKI = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKI_API = "https://en.wikipedia.org/w/api.php"
# Sections a birder would quote, best first: how it sounds, what it looks like, how it behaves
SECTION_PREFS = [("vocal", "voice", "call", "song"), ("description", "identification", "appearance"), ("behavio",)]
UA = {"User-Agent": "quizbot/1.0 (audio quiz channel; contact benjamincrystal8@gmail.com)"}
OK_LICENSES = ("creativecommons.org/licenses/by/", "creativecommons.org/licenses/by-sa/", "publicdomain")

# (name, wikipedia title, family label, size comparison, regions, habitat, difficulty 0-3)
# regions: E = Eastern US, W = Western US, C = Central / Plains, A = all / nationwide
SPECIES = [
    ("American Robin", "American robin", "a thrush", "a robin", "A", "lawns, parks and open woodland", 0),
    ("Northern Cardinal", "Northern cardinal", "a cardinal", "a robin", "EC", "backyards, hedges and woodland edges", 0),
    ("Blue Jay", "Blue jay", "a jay, in the crow family", "a robin", "EC", "oak woods and suburban yards", 0),
    ("American Crow", "American crow", "a crow", "a small hawk", "A", "almost anywhere, from farmland to city parks", 0),
    ("Mourning Dove", "Mourning dove", "a dove", "a robin", "A", "open country, fields and backyards", 0),
    ("Black-capped Chickadee", "Black-capped chickadee", "a chickadee", "a sparrow", "EW", "mixed woods and feeders", 0),
    ("Carolina Chickadee", "Carolina chickadee", "a chickadee", "a sparrow", "E", "southeastern woods and backyards", 1),
    ("Tufted Titmouse", "Tufted titmouse", "a titmouse", "a sparrow", "E", "deciduous woods and feeders", 1),
    ("House Sparrow", "House sparrow", "an Old World sparrow", "a sparrow", "A", "cities, farms and anywhere near people", 0),
    ("Song Sparrow", "Song sparrow", "a sparrow", "a sparrow", "A", "brushy edges, marshes and gardens", 1),
    ("White-throated Sparrow", "White-throated sparrow", "a sparrow", "a sparrow", "EC", "forest edges and winter feeders", 1),
    ("Dark-eyed Junco", "Dark-eyed junco", "a sparrow", "a sparrow", "A", "forest floors and winter feeders", 1),
    ("Chipping Sparrow", "Chipping sparrow", "a sparrow", "a sparrow", "A", "open woods, lawns and parks", 1),
    ("American Goldfinch", "American goldfinch", "a finch", "a sparrow", "A", "weedy fields and feeders", 0),
    ("House Finch", "House finch", "a finch", "a sparrow", "A", "cities, suburbs and feeders", 1),
    ("Purple Finch", "Purple finch", "a finch", "a sparrow", "EW", "coniferous and mixed forests", 2),
    ("Northern Mockingbird", "Northern mockingbird", "a mockingbird", "a robin", "A", "suburbs, parks and open country", 0),
    ("Gray Catbird", "Gray catbird", "a mimic, related to mockingbirds", "a robin", "EC", "dense thickets and shrubs", 1),
    ("Brown Thrasher", "Brown thrasher", "a mimic, related to mockingbirds", "a jay", "EC", "thickets and hedgerows", 2),
    ("European Starling", "Common starling", "a starling", "a robin", "A", "cities, farms and lawns", 1),
    ("Red-winged Blackbird", "Red-winged blackbird", "a blackbird", "a robin", "A", "marshes and wet fields", 0),
    ("Common Grackle", "Common grackle", "a blackbird", "a jay", "EC", "lawns, parks and farm fields", 1),
    ("Brown-headed Cowbird", "Brown-headed cowbird", "a blackbird", "a sparrow", "A", "fields, pastures and feeders", 2),
    ("Baltimore Oriole", "Baltimore oriole", "an oriole", "a robin", "EC", "tall deciduous trees in open woods", 1),
    ("Eastern Bluebird", "Eastern bluebird", "a thrush", "a sparrow", "EC", "open fields with scattered trees", 1),
    ("Western Bluebird", "Western bluebird", "a thrush", "a sparrow", "W", "open woodlands and orchards", 2),
    ("Wood Thrush", "Wood thrush", "a thrush", "a robin", "E", "the shady floor of mature forests", 2),
    ("Hermit Thrush", "Hermit thrush", "a thrush", "a sparrow", "A", "forest understory", 2),
    ("Carolina Wren", "Carolina wren", "a wren", "a sparrow", "E", "brushy yards and woodland tangles", 1),
    ("House Wren", "House wren", "a wren", "a sparrow", "A", "backyards, shrubs and nest boxes", 1),
    ("Winter Wren", "Winter wren", "a wren", "a chickadee", "E", "mossy forest floors near streams", 3),
    ("Bewick's Wren", "Bewick's wren", "a wren", "a sparrow", "W", "brushy areas and open woods", 2),
    ("Cactus Wren", "Cactus wren", "a wren", "a robin", "W", "deserts with cactus and thorny scrub", 2),
    ("Red-eyed Vireo", "Red-eyed vireo", "a vireo", "a sparrow", "EC", "the canopy of deciduous forests", 2),
    ("Warbling Vireo", "Warbling vireo", "a vireo", "a sparrow", "A", "streamside trees and open woods", 3),
    ("Yellow Warbler", "American yellow warbler", "a warbler", "a chickadee", "A", "willows and wet thickets", 2),
    ("Common Yellowthroat", "Common yellowthroat", "a warbler", "a chickadee", "A", "marshes and wet brushy fields", 2),
    ("Yellow-rumped Warbler", "Yellow-rumped warbler", "a warbler", "a chickadee", "A", "conifer forests, and almost anywhere in winter", 2),
    ("Ovenbird", "Ovenbird", "a warbler", "a sparrow", "E", "the floor of mature deciduous forests", 2),
    ("Black-throated Green Warbler", "Black-throated green warbler", "a warbler", "a chickadee", "E", "coniferous and mixed forests", 3),
    ("Pine Warbler", "Pine warbler", "a warbler", "a chickadee", "E", "pine forests", 3),
    ("Scarlet Tanager", "Scarlet tanager", "a tanager", "a sparrow", "E", "the canopy of eastern forests", 2),
    ("Western Tanager", "Western tanager", "a tanager", "a sparrow", "W", "open conifer forests in the mountains", 2),
    ("Rose-breasted Grosbeak", "Rose-breasted grosbeak", "a grosbeak", "a robin", "EC", "deciduous woods and feeders", 2),
    ("Black-headed Grosbeak", "Black-headed grosbeak", "a grosbeak", "a robin", "W", "deciduous woods and streamsides", 2),
    ("Indigo Bunting", "Indigo bunting", "a bunting", "a sparrow", "EC", "brushy fields and roadsides", 1),
    ("Eastern Towhee", "Eastern towhee", "a large sparrow", "a robin", "E", "thickets and forest edges", 2),
    ("Spotted Towhee", "Spotted towhee", "a large sparrow", "a robin", "W", "dry thickets and chaparral", 2),
    ("Eastern Phoebe", "Eastern phoebe", "a flycatcher", "a sparrow", "EC", "bridges, barns and streamsides", 2),
    ("Eastern Wood-Pewee", "Eastern wood pewee", "a flycatcher", "a sparrow", "E", "forest clearings and edges", 2),
    ("Great Crested Flycatcher", "Great crested flycatcher", "a flycatcher", "a robin", "EC", "deciduous forests", 2),
    ("Eastern Kingbird", "Eastern kingbird", "a flycatcher", "a robin", "EC", "open fields with perches", 2),
    ("Western Kingbird", "Western kingbird", "a flycatcher", "a robin", "WC", "open country, ranches and roadsides", 2),
    ("Cedar Waxwing", "Cedar waxwing", "a waxwing", "a sparrow", "A", "berry trees and orchards", 1),
    ("Downy Woodpecker", "Downy woodpecker", "a woodpecker", "a sparrow", "A", "woods, parks and feeders", 1),
    ("Hairy Woodpecker", "Hairy woodpecker", "a woodpecker", "a robin", "A", "mature forests", 2),
    ("Red-bellied Woodpecker", "Red-bellied woodpecker", "a woodpecker", "a robin", "EC", "woodlands and suburban yards", 1),
    ("Pileated Woodpecker", "Pileated woodpecker", "a woodpecker", "a crow", "EW", "mature forests with big dead trees", 1),
    ("Northern Flicker", "Northern flicker", "a woodpecker", "a jay", "A", "open woods and lawns, often on the ground", 1),
    ("Red-headed Woodpecker", "Red-headed woodpecker", "a woodpecker", "a robin", "EC", "open woods and groves", 2),
    ("Acorn Woodpecker", "Acorn woodpecker", "a woodpecker", "a robin", "W", "oak woodlands", 2),
    ("White-breasted Nuthatch", "White-breasted nuthatch", "a nuthatch", "a sparrow", "A", "deciduous woods and feeders", 1),
    ("Red-breasted Nuthatch", "Red-breasted nuthatch", "a nuthatch", "a chickadee", "A", "conifer forests", 2),
    ("Ruby-crowned Kinglet", "Ruby-crowned kinglet", "a kinglet", "a chickadee", "A", "conifers, and brushy areas in winter", 3),
    ("Golden-crowned Kinglet", "Golden-crowned kinglet", "a kinglet", "a chickadee", "A", "dense conifer forests", 3),
    ("Blue-gray Gnatcatcher", "Blue-gray gnatcatcher", "a gnatcatcher", "a chickadee", "A", "open woods and scrub", 3),
    ("Ruby-throated Hummingbird", "Ruby-throated hummingbird", "a hummingbird", "a large insect", "EC", "gardens and forest edges", 2),
    ("Anna's Hummingbird", "Anna's hummingbird", "a hummingbird", "a large insect", "W", "gardens and chaparral on the West Coast", 2),
    ("Killdeer", "Killdeer", "a plover, a kind of shorebird", "a robin", "A", "fields, parking lots and shorelines", 1),
    ("American Woodcock", "American woodcock", "a shorebird that lives in the woods", "a robin", "E", "wet thickets and forest edges at dusk", 2),
    ("Sandhill Crane", "Sandhill crane", "a crane", "a large heron", "WC", "marshes, prairies and farm fields", 1),
    ("Great Blue Heron", "Great blue heron", "a heron", "a very large bird, with a six-foot wingspan", "A", "lakeshores, marshes and rivers", 1),
    ("Canada Goose", "Canada goose", "a goose", "a goose", "A", "lakes, parks and golf courses", 0),
    ("Mallard", "Mallard", "a dabbling duck", "a duck", "A", "ponds, lakes and marshes", 0),
    ("Wood Duck", "Wood duck", "a dabbling duck", "a duck", "EW", "wooded swamps and ponds", 1),
    ("Common Loon", "Common loon", "a loon", "a goose", "EW", "northern lakes", 0),
    ("Pied-billed Grebe", "Pied-billed grebe", "a grebe", "a small duck", "A", "ponds and marshes", 3),
    ("Trumpeter Swan", "Trumpeter swan", "a swan", "a swan", "WC", "lakes and marshes", 2),
    ("Wild Turkey", "Wild turkey", "a turkey", "a turkey", "A", "open woods and fields", 0),
    ("Northern Bobwhite", "Northern bobwhite", "a quail", "a robin", "EC", "grassy fields and brushy pastures", 1),
    ("California Quail", "California quail", "a quail", "a robin", "W", "brushy areas and suburbs on the West Coast", 1),
    ("Ring-necked Pheasant", "Common pheasant", "a pheasant", "a chicken", "C", "farm fields and grassland", 1),
    ("Greater Prairie-Chicken", "Greater prairie chicken", "a grouse", "a chicken", "C", "tallgrass prairie", 2),
    ("Ruffed Grouse", "Ruffed grouse", "a grouse", "a chicken", "EW", "young forests", 2),
    ("Red-tailed Hawk", "Red-tailed hawk", "a hawk", "a large hawk", "A", "open country with perches, and highway edges", 0),
    ("Bald Eagle", "Bald eagle", "an eagle", "a very large bird of prey", "A", "lakes, rivers and coasts", 0),
    ("Osprey", "Osprey", "a fish-eating raptor", "a large hawk", "A", "lakes, rivers and coastlines", 1),
    ("American Kestrel", "American kestrel", "a falcon", "a dove", "A", "open fields, perched on wires", 1),
    ("Peregrine Falcon", "Peregrine falcon", "a falcon", "a crow", "A", "cliffs, bridges and city skyscrapers", 1),
    ("Cooper's Hawk", "Cooper's hawk", "a hawk", "a crow", "A", "woods and suburban yards with feeders", 2),
    ("Red-shouldered Hawk", "Red-shouldered hawk", "a hawk", "a crow", "EW", "wet woods and swamps", 2),
    ("Turkey Vulture", "Turkey vulture", "a vulture", "an eagle", "A", "open country, soaring overhead", 2),
    ("Great Horned Owl", "Great horned owl", "an owl", "a large hawk", "A", "woods, parks and deserts", 0),
    ("Barred Owl", "Barred owl", "an owl", "a crow", "EW", "swamps and mature forests", 0),
    ("Eastern Screech-Owl", "Eastern screech owl", "an owl", "a robin", "EC", "wooded suburbs and parks", 1),
    ("Western Screech-Owl", "Western screech owl", "an owl", "a robin", "W", "open woods and streamside groves", 2),
    ("Barn Owl", "Barn owl", "an owl", "a crow", "A", "barns, fields and grasslands", 1),
    ("Burrowing Owl", "Burrowing owl", "an owl", "a robin", "WC", "prairies and deserts, living in burrows", 2),
    ("Northern Saw-whet Owl", "Northern saw-whet owl", "an owl", "a robin", "EW", "dense conifer forests", 2),
    ("Common Raven", "Common raven", "a corvid, the largest songbird", "a hawk", "W", "mountains, deserts and coasts", 1),
    ("Steller's Jay", "Steller's jay", "a jay", "a robin", "W", "mountain conifer forests", 1),
    ("California Scrub-Jay", "California scrub jay", "a jay", "a robin", "W", "oak scrub and suburbs on the West Coast", 1),
    ("Black-billed Magpie", "Black-billed magpie", "a magpie, in the crow family", "a crow", "WC", "ranches and open country in the West", 1),
    ("Western Meadowlark", "Western meadowlark", "a meadowlark, in the blackbird family", "a robin", "WC", "grasslands and prairies", 1),
    ("Eastern Meadowlark", "Eastern meadowlark", "a meadowlark, in the blackbird family", "a robin", "EC", "pastures and hayfields", 1),
    ("Bobolink", "Bobolink", "a blackbird", "a sparrow", "EC", "hayfields and tall grass", 3),
    ("Horned Lark", "Horned lark", "a lark", "a sparrow", "A", "bare ground, fields and airports", 3),
    ("Loggerhead Shrike", "Loggerhead shrike", "a shrike", "a robin", "WC", "open country with thorny shrubs", 3),
    ("Belted Kingfisher", "Belted kingfisher", "a kingfisher", "a jay", "A", "streams, lakes and coasts", 1),
    ("Chimney Swift", "Chimney swift", "a swift", "a sparrow", "EC", "the sky over towns, roosting in chimneys", 2),
    ("Barn Swallow", "Barn swallow", "a swallow", "a sparrow", "A", "barns, bridges and open fields", 1),
    ("Tree Swallow", "Tree swallow", "a swallow", "a sparrow", "A", "fields and marshes near water", 2),
    ("Purple Martin", "Purple martin", "a swallow", "a robin", "EC", "martin houses near open water", 2),
    ("Common Nighthawk", "Common nighthawk", "a nightjar", "a robin", "A", "the evening sky over towns and open country", 2),
    ("Eastern Whip-poor-will", "Eastern whip-poor-will", "a nightjar", "a robin", "E", "dry woods at night", 1),
    ("Chuck-will's-widow", "Chuck-will's-widow", "a nightjar", "a jay", "E", "southern pine and oak woods at night", 2),
    ("Yellow-billed Cuckoo", "Yellow-billed cuckoo", "a cuckoo", "a jay", "EC", "dense deciduous woods", 3),
    ("Greater Roadrunner", "Greater roadrunner", "a cuckoo that runs", "a crow", "W", "deserts and scrub in the Southwest", 1),
    ("Gambel's Quail", "Gambel's quail", "a quail", "a robin", "W", "desert scrub in the Southwest", 2),
    ("Curve-billed Thrasher", "Curve-billed thrasher", "a thrasher", "a robin", "W", "desert scrub with cactus", 3),
    ("Verdin", "Verdin", "a tiny songbird", "a chickadee", "W", "desert scrub in the Southwest", 3),
    ("Laughing Gull", "Laughing gull", "a gull", "a crow", "E", "Atlantic and Gulf beaches", 1),
    ("Ring-billed Gull", "Ring-billed gull", "a gull", "a crow", "A", "parking lots, beaches and lakes", 2),
    ("Herring Gull", "American herring gull", "a gull", "a large gull", "A", "coasts, lakes and dumps", 1),
    ("Brown Pelican", "Brown pelican", "a pelican", "a very large bird", "A", "ocean coasts", 1),
    ("Willet", "Willet", "a sandpiper", "a crow", "A", "beaches and salt marshes", 3),
    ("Spotted Sandpiper", "Spotted sandpiper", "a sandpiper", "a robin", "A", "shorelines of streams and ponds", 3),
    ("American Oystercatcher", "American oystercatcher", "a shorebird", "a crow", "E", "Atlantic and Gulf beaches", 2),
    ("Black-necked Stilt", "Black-necked stilt", "a shorebird on very long legs", "a crow", "WC", "shallow ponds and marshes", 3),
    ("American Coot", "American coot", "a rail", "a small duck", "A", "ponds, lakes and marshes", 2),
    ("Virginia Rail", "Virginia rail", "a rail", "a robin", "A", "dense freshwater marshes", 3),
    ("Sora", "Sora", "a rail", "a robin", "A", "marshes with cattails", 3),
    ("Snow Goose", "Snow goose", "a goose", "a goose", "WC", "farm fields and marshes in huge winter flocks", 2),
    ("Northern Pintail", "Northern pintail", "a dabbling duck", "a duck", "A", "marshes and prairie potholes", 3),
    ("Hooded Merganser", "Hooded merganser", "a diving duck", "a small duck", "A", "wooded ponds and rivers", 3),
    ("Tundra Swan", "Tundra swan", "a swan", "a swan", "EW", "lakes and bays in winter", 3),
]

FAMILY_THEMES = {
    "owls": ("Owls", ("an owl",)),
    "raptors": ("Birds of Prey", ("a hawk", "an eagle", "a falcon", "a fish-eating raptor", "a vulture")),
    "waterfowl": ("Ducks, Geese and Swans", ("a dabbling duck", "a diving duck", "a goose", "a swan", "a loon", "a grebe")),
    "woodpeckers": ("Woodpeckers", ("a woodpecker",)),
    "sparrows-finches": ("Sparrows and Finches", ("a sparrow", "a large sparrow", "a finch", "a bunting", "a grosbeak", "an Old World sparrow")),
    "warblers": ("Warblers and Vireos", ("a warbler", "a vireo", "a kinglet", "a gnatcatcher")),
    "corvids": ("Crows and Jays", ("a crow", "a jay", "a jay, in the crow family", "a corvid, the largest songbird", "a magpie, in the crow family")),
    "shorebirds": ("Shorebirds and Waders", ("a sandpiper", "a shorebird", "a shorebird on very long legs", "a plover, a kind of shorebird",
                                            "a shorebird that lives in the woods", "a heron", "a crane", "a rail", "a gull", "a pelican")),
    "thrushes-mimics": ("Thrushes and Mimics", ("a thrush", "a mockingbird", "a mimic, related to mockingbirds", "a thrasher")),
    "game-birds": ("Game Birds", ("a turkey", "a quail", "a pheasant", "a grouse")),
    "night": ("Birds of the Night", ("an owl", "a nightjar")),
}
REGION_THEMES = {"east": ("Eastern US Birds", "E"), "west": ("Western US Birds", "W"), "central": ("Birds of the Plains", "C")}
REGION_NAMES = {"E": "the Eastern United States", "W": "the Western United States", "C": "the Great Plains"}


class BirdsPack(Pack):
    name = "birds"
    display_name = "Guess That Bird"
    noun = "bird"
    question_line = "Round {n}."
    reveal_line = "It's the {answer}."
    opening_line = "How fast can you name this bird?"
    channel_blurb = "Can you name the bird from its call alone?"
    accent_color = (120, 200, 140)
    bg_color = (18, 30, 24)
    pronunciations = {"Pileated": "PIE-lee-ay-ted", "Phoebe": "FEE-bee", "Pewee": "PEE-wee", "Vireo": "VEER-ee-oh",
                      "Towhee": "TOH-hee", "Grosbeak": "GROHS-beek", "Bobwhite": "BOB-white", "Killdeer": "KILL-deer",
                      "Loon": "loon", "Grebe": "greeb", "Junco": "JUN-koh", "Merganser": "mer-GAN-ser",
                      "Oystercatcher": "OY-ster-catch-er", "Chuck-will's-widow": "chuck-WILLS-widow",
                      "Whip-poor-will": "WHIP-poor-will", "Verdin": "VER-din", "Sora": "SOR-uh", "Willet": "WILL-it"}

    def themes(self) -> list[dict]:
        out = [{"id": "backyard", "label": "Backyard Birds", "max_difficulty": 1}]
        out += [{"id": k, "label": v[0], "families": v[1]} for k, v in FAMILY_THEMES.items()]
        out += [{"id": k, "label": v[0], "region": v[1]} for k, v in REGION_THEMES.items()]
        out.append({"id": "all", "label": "Birds of the United States"})
        return out

    def famous(self, item: QuizItem) -> bool:
        return item.difficulty <= 1

    def candidates(self, theme: dict) -> list[QuizItem]:
        items = []
        for row in SPECIES:
            name, wiki, family, size, regions, habitat, diff = row
            if "families" in theme and family not in theme["families"]:
                continue
            if "region" in theme and theme["region"] not in regions and regions != "A":
                continue
            if "max_difficulty" in theme and diff > theme["max_difficulty"]:
                continue
            try:
                item = self._item(row, theme)
            except Exception as e:
                print(f"  [birds] skip {name}: {e}")
                continue
            if item:
                items.append(item)
        return items

    # ---- data -----------------------------------------------------------------
    def _xc(self, name: str) -> dict | None:
        """Best openly-licensed xeno-canto recording of this species from the US: quality A/B, 4-40 s."""
        key = __import__("os").environ.get("XC_API_KEY")
        if not key:
            raise RuntimeError("XC_API_KEY not set")
        p = self.cache / f"xc-{slug(name)}.json"
        if p.exists():
            return json.loads(p.read_text())
        q = f'en:"{name}" cnt:"United States" q:A'
        r = requests.get(XC_API, params={"query": q, "key": key}, headers=UA, timeout=60)
        r.raise_for_status()
        recs = r.json().get("recordings", [])
        if len(recs) < 3:                      # fall back to any quality and any country
            r = requests.get(XC_API, params={"query": f'en:"{name}"', "key": key}, headers=UA, timeout=60)
            r.raise_for_status()
            recs += r.json().get("recordings", [])

        def secs(rec):
            try:
                m, s = rec.get("length", "0:0").split(":")
                return int(m) * 60 + int(s)
            except Exception:
                return 0
        ok = [x for x in recs if any(t in (x.get("lic") or "") for t in OK_LICENSES)
              and 4 <= secs(x) <= 40 and x.get("file")
              and "nd" not in (x.get("lic") or "").split("/")[-3:-1]]
        ok.sort(key=lambda x: (x.get("q", "Z"), -secs(x)))          # best quality first, longer first
        best = ok[0] if ok else None
        p.write_text(json.dumps(best))
        return best

    def _wiki(self, title: str) -> dict:
        p = self.cache / f"wiki2-{slug(title)}.json"
        if p.exists():
            return json.loads(p.read_text())
        r = requests.get(WIKI + title.replace(" ", "_"), headers=UA, timeout=30)
        r.raise_for_status()
        d = r.json()
        keep = {"extract": d.get("extract", ""), "image": (d.get("originalimage") or d.get("thumbnail") or {}).get("source"),
                "url": (d.get("content_urls") or {}).get("desktop", {}).get("page", ""), "sections": {}}
        try:                                               # full article text, split into sections
            r2 = requests.get(WIKI_API, params={"action": "query", "prop": "extracts", "explaintext": 1, "format": "json",
                                                "redirects": 1, "titles": title}, headers=UA, timeout=30)
            page = next(iter(r2.json()["query"]["pages"].values()))
            cur, buf = "lead", []
            for line in page.get("extract", "").splitlines():
                m = re.match(r"^=+\s*(.+?)\s*=+$", line.strip())
                if m:
                    keep["sections"][cur] = " ".join(buf).strip()
                    cur, buf = m.group(1).lower(), []
                elif line.strip():
                    buf.append(line.strip())
            keep["sections"][cur] = " ".join(buf).strip()
        except Exception as e:
            print(f"  [birds] sections unavailable for {title}: {str(e)[:80]}")
        p.write_text(json.dumps(keep))
        return keep

    def _audio(self, rec: dict, name: str) -> Path:
        """Download, trim to the first 4 s of the recording, fade, 48 kHz."""
        out = self.cache / f"call-{slug(name)}.wav"
        if out.exists():
            return out
        raw = self.cache / f"call-{slug(name)}.src"
        if not raw.exists():
            r = requests.get(rec["file"], headers=UA, timeout=120)
            r.raise_for_status()
            raw.write_bytes(r.content)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-t", "4",
                        "-af", "highpass=f=250,afade=t=in:d=0.05,afade=t=out:st=3.6:d=0.4",
                        "-ar", "48000", "-ac", "2", str(out)], check=True)
        return out

    def _photo(self, url: str, name: str) -> Path:
        """Square-crop the Wikipedia photo and mask it to a circle with a soft edge (RGBA) for the reveal."""
        out = self.cache / f"photo-{slug(name)}.png"
        if out.exists():
            return out
        raw = self.cache / f"photo-{slug(name)}.src"
        if not raw.exists():
            r = requests.get(url, headers=UA, timeout=60)
            r.raise_for_status()
            raw.write_bytes(r.content)
        im = Image.open(raw).convert("RGB")
        s = min(im.size)
        im = im.crop(((im.width - s) // 2, (im.height - s) // 2 - int(s * 0.05) if im.height > s else 0,
                      (im.width - s) // 2 + s, ((im.height - s) // 2 - int(s * 0.05) if im.height > s else 0) + s))
        im = im.resize((900, 900), Image.LANCZOS)
        mask = Image.new("L", (900, 900), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 899, 899), fill=255)
        rgba = im.convert("RGBA")
        rgba.putalpha(mask)
        rgba.save(out)
        return out

    def _item(self, row, theme: dict) -> QuizItem | None:
        name, wiki, family, size, regions, habitat, diff = row
        rec = self._xc(name)
        if not rec:
            print(f"  [birds] no open-licence recording for {name}")
            return None
        info = self._wiki(wiki)
        if not info.get("image"):
            return None
        audio = self._audio(rec, name)
        photo = self._photo(info["image"], name)

        where = ", ".join(REGION_NAMES[c] for c in regions if c in REGION_NAMES) if regions != "A" else "most of the United States"
        region_hint = f"It lives in {where}, in {habitat}."
        fam_word = re.sub(r"^(a|an)\s+", "", family).split(",")[0].split(" ")[0]
        if fam_word and fam_word in size:                       # "a sparrow … the size of a sparrow"
            family_hint = f"It's {family}."
        else:
            family_hint = f"It's {family}, about the size of {size}."
        guide_src, guide_kind = field_guide_text(info, name)
        guide = redact(first_sentences(guide_src, 2), name)
        lead_in = {"voice": "Birders describe the call like this:", "looks": "From the field guide:",
                   "behaviour": "From the field guide:"}[guide_kind]
        hints = [region_hint, family_hint, f"{lead_in} {guide}"]
        aka = aliases(info["extract"], name)
        if "region" in theme:
            hints.remove(region_hint)
        elif "families" in theme:
            hints.remove(family_hint)

        credit = f"{name}: recording XC{rec.get('id')} by {rec.get('rec')} (xeno-canto, {lic_short(rec.get('lic', ''))}); photo via Wikipedia"
        return QuizItem(
            id=f"birds:{slug(name)}", answer=name, prompt_audio=audio, reveal_image=photo,
            fact=f"The {name}, {family}.", subtitle=family.capitalize(), hints=hints,
            tint=(110, 190, 130) if "W" not in regions else (200, 170, 90),
            shape_hint=False,                      # photos have no silhouette; the last hint is the field-guide line
            difficulty=float(diff), pronunciation=None,
            meta={"family": family, "regions": regions, "credit": credit, "wiki": info.get("url", ""),
                  "entry": redact_clean_lead(info["extract"]), "aka": aka},
        )


def field_guide_text(info: dict, name: str) -> tuple[str, str]:
    """Pick the most birder-like passage: the call description, else the physical description, else behaviour,
    else the lead paragraph with its 'also known as' clause stripped."""
    secs = info.get("sections") or {}
    for keys, kind in zip(SECTION_PREFS, ("voice", "looks", "behaviour")):
        for title, text in secs.items():
            if any(k in title for k in keys) and len(text) > 80:
                return text, kind
    return redact_clean_lead(info.get("extract", "")), "looks"


def redact_clean_lead(text: str) -> str:
    """Lead paragraph without the ', also known as …,' clause (those names move to the reveal)."""
    t = re.sub(r",?\s*(also|sometimes|formerly|commonly|colloquially) (known|called|referred to) (as )?[^.]*?(?=, is | is |\.)", "", text, flags=re.I)
    return first_sentences(t, 3)


def aliases(text: str, name: str) -> list[str]:
    """'also known as the northern barred owl, striped owl, or hoot owl' → ['northern barred owl', 'striped owl', 'hoot owl']"""
    m = re.search(r"(?:also|sometimes|commonly|colloquially) (?:known|called|referred to) (?:as )?(?:the )?([^.]*?)(?=, is | is |\.)", text, flags=re.I)
    if not m:
        return []
    raw = re.sub(r"\bor more informally,?\b", ",", m.group(1), flags=re.I)
    parts = re.split(r",\s*|\s+or\s+", raw)
    out = []
    for x in parts:
        x = re.sub(r"^(the|an?)\s+", "", x.strip(" ,"), flags=re.I).strip()
        if x and x.lower() != name.lower() and len(x) < 40 and x not in out:
            out.append(x)
    return out[:4]


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def first_sentences(text: str, n: int) -> str:
    parts = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    return " ".join(parts[:n])


def redact(text: str, name: str) -> str:
    """Hide the bird's name. The full name becomes 'this bird'; a lone distinctive word of it (junco, towhee,
    'the robin') becomes plain 'bird'/'birds', so 'a species of junco' reads 'a species of bird'."""
    t = re.sub(rf"\b(a|an|the)\s+{re.escape(name)}s?\b", "this bird", text, flags=re.I)
    t = re.sub(rf"\b{re.escape(name)}s?\b", "this bird", t, flags=re.I)
    generic = {"american", "common", "northern", "eastern", "western", "great", "house", "black", "white", "red",
               "blue", "yellow", "brown", "gray", "grey", "green", "golden", "wood", "song", "winter", "tree"}
    for w in re.split(r"[\s\-']+", name):
        if len(w) >= 4 and w.lower() not in generic:
            t = re.sub(rf"\b{re.escape(w)}s\b", "birds", t, flags=re.I)
            t = re.sub(rf"\b{re.escape(w)}\b", "bird", t, flags=re.I)
    t = re.sub(r"\([^)]*\)", "", t)                  # drop scientific names in parentheses
    t = re.sub(r"\s+", " ", t).replace(" ,", ",").replace(" .", ".").strip()
    t = re.sub(r"\bthis bird, (is|was|has)\b", r"this bird \1", t, flags=re.I)      # leftover comma after a stripped clause
    t = re.sub(r"\bthis bird is a (species|kind|type) of bird\b", "this bird is a bird", t, flags=re.I)
    t = re.sub(r"\bis a bird, (a group|a genus|a family) of\b", r"belongs to \1 of", t, flags=re.I)
    return re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)


def lic_short(url: str) -> str:
    m = re.search(r"licenses/([a-z\-]+)/(\d\.\d)", url)
    return f"CC {m.group(1).upper()} {m.group(2)}" if m else "CC"
