"""Hint visuals: small, standardised graphics drawn under a hint, derived only from structured item data
(never from prose), so they are right every time and never leak an answer.

  {"kind": "map",   "regions": "EW"}            → contiguous-US silhouette with the named regions lit
  {"kind": "size",  "rank": 2}                  → size ladder (sparrow · robin · crow · goose), one dot highlighted
  {"kind": "types", "types": ["Fire", "Flying"]} → one pill per type in its colour
  {"kind": "chip",  "text": "Unova"}            → a plain text chip

Packs attach them as item.meta["visuals"] = {hint_text: spec}; Scene draws the one for the current hint.
Each function returns an RGBA layer of the requested height; width follows.
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

# Contiguous US outline, (lon, lat), clockwise from the Washington coast. Hand-simplified: recognisable at
# 300 px, not a survey. Alaska and Hawaii are omitted (the bird list is lower-48).
US = [(-124.7, 48.4), (-123.3, 49.0), (-95.2, 49.0), (-94.6, 48.7), (-89.5, 48.0), (-88.4, 48.3), (-84.8, 46.9),
      (-84.5, 46.4), (-82.5, 45.3), (-82.1, 43.0), (-83.1, 42.0), (-79.0, 42.9), (-79.1, 43.6), (-76.3, 44.2),
      (-74.7, 45.0), (-71.5, 45.0), (-70.8, 45.4), (-69.2, 47.4), (-67.8, 47.1), (-67.0, 44.8), (-68.9, 44.2),
      (-70.0, 43.4), (-70.6, 41.7), (-71.9, 41.3), (-74.0, 40.6), (-74.0, 39.6), (-75.1, 38.4), (-76.0, 37.2),
      (-75.6, 35.3), (-77.9, 33.9), (-79.9, 32.8), (-81.4, 31.0), (-81.5, 30.4), (-80.1, 27.2), (-80.4, 25.2),
      (-81.3, 25.1), (-82.0, 26.5), (-82.7, 27.9), (-83.0, 29.2), (-84.3, 30.0), (-85.7, 30.2), (-87.8, 30.3),
      (-89.2, 30.2), (-89.9, 29.3), (-91.6, 29.5), (-93.8, 29.7), (-95.1, 29.0), (-96.8, 28.1), (-97.2, 26.3),
      (-97.5, 25.9), (-99.1, 26.4), (-100.3, 28.5), (-101.4, 29.8), (-102.5, 29.8), (-103.3, 29.0), (-104.5, 29.6),
      (-106.5, 31.8), (-108.2, 31.3), (-111.1, 31.3), (-114.8, 32.5), (-117.1, 32.5), (-118.5, 34.0), (-120.6, 34.6),
      (-121.9, 36.6), (-122.5, 37.8), (-124.0, 40.0), (-124.4, 42.0), (-123.9, 46.2), (-124.7, 48.4)]
LON0, LON1, LAT0, LAT1 = -125.0, -66.5, 24.5, 49.5
# region bands by longitude: W = Rockies and westward, C = the Plains, E = Mississippi eastward
BANDS = {"W": (LON0, -104.0), "C": (-104.0, -90.0), "E": (-90.0, LON1)}

SIZE_LADDER = ["sparrow", "robin", "crow", "goose"]
SIZE_WORDS = {  # keywords in a pack's size comparison → rung on the ladder
    0: ("sparrow", "chickadee", "finch", "warbler", "hummingbird", "wren", "kinglet"),
    1: ("robin", "jay", "starling", "cardinal", "thrush", "blackbird", "pigeon", "dove", "woodpecker", "killdeer"),
    2: ("crow", "duck", "mallard", "hawk", "gull", "owl", "chicken", "falcon", "kestrel"),
    3: ("goose", "heron", "eagle", "turkey", "swan", "pelican", "crane", "vulture", "loon"),
}

TYPE_COLORS = {
    "normal": (168, 168, 120), "fire": (240, 128, 48), "water": (104, 144, 240), "grass": (120, 200, 80),
    "electric": (248, 208, 48), "ice": (152, 216, 216), "fighting": (192, 48, 40), "poison": (160, 64, 160),
    "ground": (224, 192, 104), "flying": (168, 144, 240), "psychic": (248, 88, 136), "bug": (168, 184, 32),
    "rock": (184, 160, 56), "ghost": (112, 88, 152), "dragon": (112, 56, 248), "dark": (112, 88, 72),
    "steel": (184, 184, 208), "fairy": (238, 153, 172),
}


def size_rank(size_text: str) -> int | None:
    s = size_text.lower()
    for rank, words in SIZE_WORDS.items():
        if any(w in s for w in words):
            return rank
    return None


def _font(size: int, bold=True, fonts_dir=None) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(str(fonts_dir / name) if fonts_dir else name, size)
    except Exception:
        return ImageFont.load_default()


def us_map(regions: str, height: int, accent: tuple, dim: tuple = (70, 72, 90)) -> Image.Image:
    """The lower 48 with the given regions (letters of E/W/C, or A for all) filled in the accent colour."""
    scale = 4
    h = height * scale
    w = int(h * (LON1 - LON0) / (LAT1 - LAT0) * 0.78)        # ~0.78: rough cos(lat) foreshortening at 40° N

    def xy(lon, lat):
        return ((lon - LON0) / (LON1 - LON0) * w, (LAT1 - lat) / (LAT1 - LAT0) * h)
    outline = Image.new("L", (w, h), 0)
    ImageDraw.Draw(outline).polygon([xy(*p) for p in US], fill=255)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    layer.paste(Image.new("RGBA", (w, h), (*dim, 255)), mask=outline)
    lit = set("EWC") if regions == "A" else set(regions)
    for code in lit:
        if code not in BANDS:
            continue
        lo, hi = BANDS[code]
        band = Image.new("L", (w, h), 0)
        ImageDraw.Draw(band).rectangle((xy(lo, LAT1)[0], 0, xy(hi, LAT1)[0], h), fill=255)
        band = Image.composite(band, Image.new("L", (w, h), 0), outline)
        layer.paste(Image.new("RGBA", (w, h), (*accent, 255)), mask=band)
    # thin outline so the silhouette reads against any background
    edge = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(edge).polygon([xy(*p) for p in US], outline=(230, 230, 238, 255), width=scale * 2)
    layer.alpha_composite(edge)
    return layer.resize((w // scale, h // scale), Image.LANCZOS)


def size_ladder(rank: int, height: int, accent: tuple, fonts_dir=None, dim: tuple = (70, 72, 90)) -> Image.Image:
    """Four dots of growing size (sparrow → goose) with the matching rung lit and labelled."""
    label_h = int(height * 0.28)
    dot_zone = height - label_h
    radii = [int(dot_zone * r / 2) for r in (0.28, 0.46, 0.68, 0.98)]
    gap = int(dot_zone * 0.45)
    w = sum(2 * r for r in radii) + gap * (len(radii) + 1)
    img = Image.new("RGBA", (w, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = _font(max(14, int(label_h * 0.7)), True, fonts_dir)
    x = gap
    for i, r in enumerate(radii):
        cx, cy = x + r, dot_zone // 2
        col = accent if i == rank else dim
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*col, 255))
        if i == rank:
            d.ellipse((cx - r - 4, cy - r - 4, cx + r + 4, cy + r + 4), outline=(255, 255, 255, 255), width=3)
            d.text((cx, dot_zone + label_h // 2), SIZE_LADDER[i], font=f, fill=(240, 240, 246, 255), anchor="mm")
        x += 2 * r + gap
    return img


def type_pills(types: list[str], height: int, fonts_dir=None) -> Image.Image:
    f = _font(int(height * 0.5), True, fonts_dir)
    pad, gap = int(height * 0.55), int(height * 0.3)
    widths = [int(f.getlength(t.upper())) + pad * 2 for t in types]
    img = Image.new("RGBA", (sum(widths) + gap * (len(types) - 1), height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = 0
    for t, w in zip(types, widths):
        col = TYPE_COLORS.get(t.lower(), (168, 168, 120))
        d.rounded_rectangle((x, 0, x + w, height), radius=height // 2, fill=(*col, 255))
        d.text((x + w / 2, height / 2), t.upper(), font=f, fill=(14, 14, 20, 255), anchor="mm")
        x += w + gap
    return img


def chip(text: str, height: int, fonts_dir=None, fill=(60, 62, 80)) -> Image.Image:
    f = _font(int(height * 0.5), True, fonts_dir)
    w = int(f.getlength(text)) + int(height * 1.1)
    img = Image.new("RGBA", (w, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w, height), radius=height // 2, fill=(*fill, 255))
    d.text((w / 2, height / 2), text, font=f, fill=(240, 240, 246, 255), anchor="mm")
    return img


def render(spec: dict, height: int, accent: tuple, fonts_dir=None) -> Image.Image | None:
    k = spec.get("kind")
    if k == "map":
        return us_map(spec.get("regions", "A"), height, accent)
    if k == "size" and spec.get("rank") is not None:
        return size_ladder(int(spec["rank"]), height, accent, fonts_dir)
    if k == "types" and spec.get("types"):
        return type_pills(list(spec["types"])[:2], int(height * 0.42), fonts_dir)
    if k == "chip" and spec.get("text"):
        return chip(str(spec["text"]), int(height * 0.42), fonts_dir)
    return None
