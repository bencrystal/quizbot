"""Frame-based scene drawing. A Scene knows the round's audio timeline and draws any instant t.

Visual language: dark neutral frame while you only have the sound; the whole frame tints to the item's
`tint` (its type colour) the moment hint 1 lands; hints slide in one at a time; a live waveform reacts to
the sound; the final hint morphs a '?' disc into the silhouette; reveal brings the full-colour art.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..config import Settings

FONT_BOLD = "DejaVuSans-Bold.ttf"
FONT_REG = "DejaVuSans.ttf"
WHITE = (255, 255, 255)
NEUTRAL_BG = (16, 18, 28)
NEUTRAL_ACCENT = (210, 214, 228)


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_rgb(a, b, t):
    return tuple(int(lerp(x, y, t)) for x, y in zip(a, b))


def ease(t: float) -> float:           # smoothstep, clamped
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


@dataclass
class Event:
    """Something that happens at time `at` on the timeline."""
    at: float
    kind: str              # 'sound' | 'hint' | 'think' | 'shape' | 'reveal' | 'intro' | 'outro' | 'status'
    dur: float = 0.0
    data: dict = field(default_factory=dict)


class Scene:
    def __init__(self, settings: Settings, channel: str):
        self.s = settings
        self.W, self.H = settings.width, settings.height
        self.portrait = self.H > self.W
        self.u = min(self.W, self.H) / 1080
        self.channel = channel
        self.fonts_dir = settings.assets_dir / "fonts"
        self._fonts: dict = {}
        self._glow: Image.Image | None = None
        self._art: dict = {}

    # ---- primitives -----------------------------------------------------------
    def font(self, size: int, bold=True) -> ImageFont.FreeTypeFont:
        k = (max(10, int(size * self.u)), bold)
        if k not in self._fonts:
            self._fonts[k] = ImageFont.truetype(str(self.fonts_dir / (FONT_BOLD if bold else FONT_REG)), k[0])
        return self._fonts[k]

    def text(self, d, xy, s, size, fill=WHITE, bold=True, anchor="mm", max_width=None, alpha=1.0):
        f = self.font(size, bold)
        if max_width:
            while f.getlength(s) > max_width and size > 18:
                size -= 3
                f = self.font(size, bold)
        if alpha < 1:
            fill = tuple(int(c * alpha) for c in fill)
        d.text(xy, s, font=f, fill=fill, anchor=anchor)

    def wrap(self, s, size, max_width, bold=False):
        f = self.font(size, bold)
        words, lines, cur = s.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if f.getlength(t) <= max_width:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def wrap_fit(self, s, size, max_width, max_lines, bold=False, min_size=26):
        """Wrap, shrinking the font until the paragraph fits in max_lines. Returns (lines, size)."""
        while True:
            lines = self.wrap(s, size, max_width, bold)
            if len(lines) <= max_lines or size <= min_size:
                return lines[:max_lines], size
            size -= 2

    def glow(self) -> Image.Image:
        """White radial glow, tinted at paste time. Rendered once at low res."""
        if self._glow is None:
            # canvas 2x the frame so the soft disc never reaches a canvas edge wherever it drifts
            w, h = self.W * 2, self.H * 2
            yy, xx = np.mgrid[0:h:4, 0:w:4].astype(np.float32)
            rr = np.hypot(xx - w / 2, yy - h / 2) / (min(self.W, self.H) * 0.45)
            g = np.exp(-rr * rr) * 255
            g += np.random.default_rng(0).uniform(-1.5, 1.5, g.shape)      # dither: kills banding rings
            small = Image.fromarray(np.clip(g, 0, 255).astype(np.uint8), "L")
            self._glow = small.resize((w, h), Image.BILINEAR)
        return self._glow

    def background(self, bg: tuple, accent: tuple, t: float) -> Image.Image:
        img = Image.new("RGB", (self.W, self.H), bg)
        # slow drift of a tinted glow, lower-left → lower-right over ~40s
        # glow centre wanders slowly around the lower half of the frame
        cxg = self.W * (0.5 + 0.35 * math.sin(t / 37 * 2 * math.pi))
        cyg = self.H * (0.72 + 0.12 * math.cos(t / 53 * 2 * math.pi))
        tint = Image.new("RGB", (self.W, self.H), lerp_rgb(bg, accent, 0.28))
        x0, y0 = int(self.W - cxg), int(self.H - cyg)
        mask = self.glow().crop((x0, y0, x0 + self.W, y0 + self.H))
        img.paste(tint, (0, 0), mask)
        d = ImageDraw.Draw(img)
        d.rectangle((0, self.H - int(12 * self.u), self.W, self.H), fill=accent)
        return img

    def art(self, path: Path, box: int, mode: str) -> Image.Image:
        """mode: 'color' | 'sil' (black silhouette) | 'mask' (L alpha)."""
        k = (str(path), box, mode)
        if k not in self._art:
            im = Image.open(path).convert("RGBA")
            im.thumbnail((box, box), Image.LANCZOS)
            canvas = Image.new("RGBA", (box, box), (0, 0, 0, 0))
            canvas.paste(im, ((box - im.width) // 2, (box - im.height) // 2), im)
            if mode == "color":
                self._art[k] = canvas
            elif mode == "mask":
                self._art[k] = canvas.getchannel("A")
            else:
                sil = Image.new("RGBA", (box, box), (0, 0, 0, 0))
                sil.paste(Image.new("RGBA", (box, box), (10, 10, 14, 255)), mask=canvas.getchannel("A"))
                self._art[k] = sil
        return self._art[k]

    def morph_mask(self, path: Path, box: int, p: float) -> Image.Image:
        """Blend a soft disc into the soft silhouette and re-threshold → organic morph."""
        k = (str(path), box, "soft")
        if k not in self._art:
            disc = Image.new("L", (box, box), 0)
            r = int(box * 0.36)
            ImageDraw.Draw(disc).ellipse((box // 2 - r, box // 2 - r, box // 2 + r, box // 2 + r), fill=255)
            blur = box * 0.035
            self._art[k] = (np.asarray(disc.filter(ImageFilter.GaussianBlur(blur)), dtype=np.float32),
                            np.asarray(self.art(path, box, "mask").filter(ImageFilter.GaussianBlur(blur)), dtype=np.float32))
        a, b = self._art[k]
        m = a * (1 - p) + b * p
        # sharpen the threshold as the morph completes so the final shape is crisp
        k_sharp = 6 + 30 * p
        out = 255 / (1 + np.exp(-(m - 128) / (255 / k_sharp)))
        return Image.fromarray(out.astype(np.uint8), "L")

    def paste_center(self, img, layer, cx, cy, mask=None):
        img.paste(layer, (int(cx - layer.width // 2), int(cy - layer.height // 2)), mask if mask is not None else layer)

    # ---- layout ---------------------------------------------------------------
    def geom(self):
        if self.portrait:
            # Shorts safe area: YouTube overlays the right ~18% (like/comment/share) from ~40% down,
            # and the bottom ~22% (title/subscribe). Everything important lives above y=0.74H, left of x=0.80W.
            cx = self.W // 2
            return dict(art=(cx, int(self.H * 0.30)), box=int(self.W * 0.62),
                        wave=(cx, int(self.H * 0.505), int(self.W * 0.52)),
                        dots=(cx, int(self.H * 0.465)),
                        text_x=int(self.W * 0.13), text_w=int(self.W * 0.74), text_y=int(self.H * 0.56), center=False,
                        name_y=int(self.H * 0.56))
        return dict(art=(int(self.W * 0.72), int(self.H * 0.46)), box=int(self.H * 0.6),
                    wave=(int(self.W * 0.72), int(self.H * 0.84), int(self.W * 0.3)),
                    dots=(int(self.W * 0.72), int(self.H * 0.79)),
                    text_x=int(self.W * 0.07), text_w=int(self.W * 0.46), text_y=int(self.H * 0.30), center=False,
                    name_y=int(self.H * 0.30))

    # ---- frame ----------------------------------------------------------------
    def draw(self, st: dict) -> Image.Image:
        """st: see Timeline.state(t)."""
        g = self.geom()
        bg = lerp_rgb(NEUTRAL_BG, lerp_rgb(NEUTRAL_BG, st["tint"], 0.14), st["tint_p"]) if st["tint"] else NEUTRAL_BG
        accent = lerp_rgb(NEUTRAL_ACCENT, st["tint"], st["tint_p"]) if st["tint"] else NEUTRAL_ACCENT
        img = self.background(bg, accent, st["t"])
        d = ImageDraw.Draw(img)
        m = int(64 * self.u)
        right = self.W - m

        if st["card"]:                               # intro / outro / score card
            title, sub, foot = st["card"]
            lines = self.wrap(title, 96, self.W - 2 * m, bold=True)
            y = self.H // 2 - int(58 * self.u) * len(lines)
            for ln in lines:
                self.text(d, (self.W // 2, y), ln, 96)
                y += int(116 * self.u)
            self.text(d, (self.W // 2, y + int(16 * self.u)), sub, 46, fill=accent, bold=False)
            if foot:
                self.text(d, (self.W // 2, self.H - int(110 * self.u)), foot, 34, fill=(190, 190, 200), bold=False)
            return img

        # header
        self.text(d, (m, m), st["header"], 36, fill=accent, anchor="lm")
        if st["points"] is not None:
            label = f"{st['points']} PT{'S' if st['points'] != 1 else ''}"
            pop = 1 + 0.25 * math.sin(math.pi * ease(st["points_age"] / 0.35)) if st["points_age"] < 0.35 else 1
            f = self.font(int(36 * pop))
            w = f.getlength(label) + int(44 * self.u * pop)
            h = int(62 * self.u * pop)
            d.rounded_rectangle((right - w, m - h // 2, right, m + h // 2), radius=h // 2, fill=accent)
            self.text(d, (right - w / 2, m), label, int(36 * pop), fill=bg)

        # art region
        cx, cy = g["art"]
        box = g["box"]
        art = st["art"]
        inner = int(box * 0.62)      # silhouette lives fully inside the disc (disc diameter = 0.72 box)
        if art:
            if st["reveal_p"] > 0:
                layer = self.art(art, box, "color")
                # grows out of the silhouette size; landscape has room for a big hero image
                sc = 0.62 + (0.60 if not self.portrait else 0.30) * ease_out(st["reveal_p"] / 0.6)
                layer = layer.resize((max(1, int(box * sc)), max(1, int(box * sc))), Image.BILINEAR)
                self.paste_center(img, layer, cx, cy)
            elif st["shape_p"] > 0:
                p = ease(st["shape_p"])
                exact = self.art(art, inner, "mask")
                if p >= 1:
                    mask = exact
                else:
                    mask = self.morph_mask(art, inner, min(1.0, p / 0.75))
                    if p > 0.65:                       # crossfade the blobby morph into the crisp silhouette
                        mask = Image.blend(mask, exact, (p - 0.65) / 0.35)
                r = int(box * 0.36)
                d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(10, 10, 14))
                self.paste_center(img, Image.new("RGB", (inner, inner), (4, 4, 8)), cx, cy, mask)
                d = ImageDraw.Draw(img)
                if st["shape_p"] < 0.5:
                    self.text(d, (cx, cy), "?", int(box * 0.45), fill=accent, alpha=1 - st["shape_p"] * 2)
            else:
                r = int(box * 0.36)
                d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(10, 10, 14))
                self.text(d, (cx, cy), "?", int(box * 0.45), fill=accent)
            # progress arc hugging the disc: fills over the whole round (stages + thinking time)
            if st["reveal_p"] == 0 and st["points"] is not None:
                r = int(box * 0.36) + int(22 * self.u)
                w = int(10 * self.u)
                bb = (cx - r, cy - r, cx + r, cy + r)
                d.ellipse(bb, outline=(50, 52, 66), width=w)
                if st["arc"] > 0:
                    d.arc(bb, start=-90, end=-90 + 360 * st["arc"], fill=accent, width=w)
                # notches at each stage boundary, centred on the stroke (PIL strokes inward from the bbox)
                rc = r - w / 2
                for k in range(st["stages"]):
                    ang = math.radians(-90 + 360 * k / st["stages"])
                    nx, ny = cx + rc * math.cos(ang), cy + rc * math.sin(ang)
                    nr = int(11 * self.u)
                    d.ellipse((nx - nr, ny - nr, nx + nr, ny + nr), fill=bg, outline=accent if 360 * k / st["stages"] <= 360 * st["arc"] else (90, 92, 110), width=max(2, int(3 * self.u)))
            # hint dots
            if st["reveal_p"] == 0 and st["stages"] > 1:
                dx_, dy_ = g["dots"]
                n = st["stages"] - 1
                gap = int(30 * self.u)
                for k in range(n):
                    x0 = dx_ + (k - (n - 1) / 2) * gap
                    rr = int(7 * self.u)
                    if k < len(st["hints"]):
                        d.ellipse((x0 - rr, dy_ - rr, x0 + rr, dy_ + rr), fill=accent)
                    else:
                        d.ellipse((x0 - rr, dy_ - rr, x0 + rr, dy_ + rr), outline=(90, 92, 110), width=max(1, int(2 * self.u)))

        # waveform (listening + thinking)
        if st["reveal_p"] == 0 and st["wave"] is not None:
            wx, wy, ww = g["wave"]
            n = 28
            bw = ww / n
            for i in range(n):
                v = st["wave"][i]
                hgt = int((6 + 90 * v) * self.u)
                x0 = wx - ww / 2 + i * bw + bw * 0.25
                col = accent if v > 0.02 else (70, 72, 90)
                d.rounded_rectangle((x0, wy - hgt / 2, x0 + bw * 0.5, wy + hgt / 2), radius=int(bw * 0.25), fill=col)

        # text column: hints (pre-reveal) or name + fact (reveal)
        x, y, anchor = g["text_x"], g["text_y"], ("mm" if g["center"] else "lm")
        if st["reveal_p"] > 0:
            y = g["name_y"]
            # landscape: bigger type, plus the Pokédex entry under the one-liner so the column isn't empty
            name_sz, fact_sz, entry_sz = (104, 36, 0) if self.portrait else (124, 44, 32)
            fact_lines = self.wrap(st["fact"], fact_sz, g["text_w"])[:4]
            entry_lines = self.wrap(st["entry"], entry_sz, g["text_w"])[:6] if entry_sz and st["entry"] else []
            if not self.portrait:
                block = (int(100 * self.u) + int(100 * self.u) + len(fact_lines) * int(fact_sz * 1.4 * self.u)
                         + (int(30 * self.u) + len(entry_lines) * int(entry_sz * 1.45 * self.u) if entry_lines else 0))
                y = g["art"][1] - block // 2 + int(30 * self.u)
            a = ease_out(st["reveal_p"] / 0.4)
            rx_, ranchor = (g["art"][0], "mm") if self.portrait else (x, anchor)
            self.text(d, (rx_, y), st["answer"], name_sz, anchor=ranchor, max_width=g["text_w"], alpha=a)
            y += int((90 if self.portrait else 100) * self.u)
            label = st["subtitle"]
            f = self.font(34)
            w = f.getlength(label) + int(44 * self.u)
            h = int(58 * self.u)
            px = rx_ - w / 2 if ranchor == "mm" else rx_
            d.rounded_rectangle((px, y - h // 2, px + w, y + h // 2), radius=h // 2,
                                fill=tuple(int(c * a) for c in accent))
            self.text(d, (px + w / 2, y), label, 34, fill=bg, alpha=a)
            y += int((90 if self.portrait else 100) * self.u)
            for ln in fact_lines:
                self.text(d, (rx_, y), ln, fact_sz, fill=(230, 230, 238), bold=False, anchor=ranchor, alpha=a)
                y += int(fact_sz * 1.4 * self.u)
            if entry_lines:
                y += int(30 * self.u)
                for ln in entry_lines:
                    self.text(d, (rx_, y), ln, entry_sz, fill=(175, 178, 196), bold=False, anchor=ranchor, alpha=a)
                    y += int(entry_sz * 1.45 * self.u)
        else:
            if st["caption"] and not st["hints"]:
                lines = self.wrap_fit(st["caption"], 56, g["text_w"], 3, bold=True)[0]
                if not self.portrait:
                    y = g["art"][1] - int(36 * self.u) * len(lines)
                for ln in lines:
                    self.text(d, (x, y), ln, 56, anchor=anchor)
                    y += int(72 * self.u)
            elif st["status"] and not st["hints"]:
                if self.portrait:
                    self.text(d, (g["art"][0], y), st["status"], 40, fill=(200, 202, 214), bold=False, anchor="mm")
                else:
                    self.text(d, (x, g["art"][1]), st["status"], 44, fill=(200, 202, 214), bold=False, anchor="lm")
            sizes = []
            for i, (hint, age) in enumerate(st["hints"]):
                s_ = hint[0].upper() + hint[1:]
                if i == len(st["hints"]) - 1:
                    lines, size = self.wrap_fit(s_, 40, g["text_w"], max_lines=7, bold=True)
                else:   # earlier hints collapse to one dim line
                    lines, size = self.wrap_fit(s_, 32, g["text_w"], max_lines=1, bold=False)
                    if lines and not s_.endswith(lines[-1]):
                        lines[-1] = lines[-1].rstrip(",.;: ") + "…"
                sizes.append((lines, size, age, i))
            latest = len(sizes) - 1
            if not self.portrait and sizes:          # centre the block vertically on the disc
                total_h = sum(len(l) * int(sz * 1.4 * self.u) + int(16 * self.u) for l, sz, _, _ in sizes)
                y = max(int(self.H * 0.14), g["art"][1] - total_h // 2)
            for lines, size, age, i in sizes:
                slide = ease_out(age / 0.45)
                a = slide * (1.0 if i == latest else 0.55)
                dx = int((1 - slide) * 60 * self.u)
                for ln in lines:
                    self.text(d, (x + dx, y), ln, size, bold=(i == latest), anchor=anchor, alpha=a)
                    y += int(size * 1.4 * self.u)
                y += int(16 * self.u)

        return img

    def thumbnail(self, title: str, tint: tuple | None, wave: np.ndarray, out: Path) -> Path:
        saved = (self.W, self.H, self.portrait, self.u, self._glow)
        self.W, self.H, self.portrait, self.u, self._glow = 1280, 720, False, 720 / 1080, None
        self._fonts.clear()
        accent = tint or NEUTRAL_ACCENT
        bg = lerp_rgb(NEUTRAL_BG, accent, 0.14) if tint else NEUTRAL_BG
        img = self.background(bg, accent, 7)
        d = ImageDraw.Draw(img)
        r = 170
        d.ellipse((930 - r, 300 - r, 930 + r, 300 + r), fill=(10, 10, 14))
        self.text(d, (930, 300), "?", 300, fill=accent)
        n = 40
        for i in range(n):
            v = float(wave[int(i * len(wave) / n)]) if len(wave) else 0.2
            h = 10 + 150 * v
            x0 = 640 + i * 14.5
            d.rounded_rectangle((x0, 560 - h / 2, x0 + 8, 560 + h / 2), radius=4, fill=accent)
        y = 150
        for ln in self.wrap(title, 100, 560, bold=True)[:4]:
            for dx, dy in ((-4, 0), (4, 0), (0, -4), (0, 4)):
                self.text(d, (60 + dx, y + dy), ln, 100, fill=(0, 0, 0), anchor="lm")
            self.text(d, (60, y), ln, 100, anchor="lm")
            y += 118
        img.save(out, quality=92)
        self.W, self.H, self.portrait, self.u, self._glow = saved
        self._fonts.clear()
        return out
