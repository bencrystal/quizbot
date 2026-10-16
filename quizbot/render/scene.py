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
        mask = self.glow().crop((int(self.W - cxg), int(self.H - cyg), int(2 * self.W - cxg), int(2 * self.H - cyg)))
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
            blur = box * 0.06
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
            return dict(art=(self.W // 2, int(self.H * 0.36)), box=int(self.W * 0.72),
                        wave=(self.W // 2, int(self.H * 0.585), int(self.W * 0.6)),
                        text_x=self.W // 2, text_w=int(self.W * 0.86), text_y=int(self.H * 0.645), center=True,
                        ring=(self.W // 2, int(self.H * 0.885), int(92 * self.u)), name_y=int(self.H * 0.64))
        return dict(art=(int(self.W * 0.72), int(self.H * 0.44)), box=int(self.H * 0.6),
                    wave=(int(self.W * 0.72), int(self.H * 0.80), int(self.W * 0.3)),
                    text_x=int(self.W * 0.065), text_w=int(self.W * 0.46), text_y=int(self.H * 0.26), center=False,
                    ring=(int(self.W * 0.16), int(self.H * 0.80), int(90 * self.u)), name_y=int(self.H * 0.28))

    # ---- frame ----------------------------------------------------------------
    def draw(self, st: dict) -> Image.Image:
        """st: see Timeline.state(t)."""
        g = self.geom()
        bg = lerp_rgb(NEUTRAL_BG, lerp_rgb(NEUTRAL_BG, st["tint"], 0.14), st["tint_p"]) if st["tint"] else NEUTRAL_BG
        accent = lerp_rgb(NEUTRAL_ACCENT, st["tint"], st["tint_p"]) if st["tint"] else NEUTRAL_ACCENT
        img = self.background(bg, accent, st["t"])
        d = ImageDraw.Draw(img)
        m = int(64 * self.u)

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
            d.rounded_rectangle((self.W - m - w, m - h // 2, self.W - m, m + h // 2), radius=h // 2, fill=accent)
            self.text(d, (self.W - m - w / 2, m), label, int(36 * pop), fill=bg)

        # art region
        cx, cy = g["art"]
        box = g["box"]
        art = st["art"]
        if art:
            if st["reveal_p"] > 0:
                layer = self.art(art, box, "color")
                sc = 0.92 + 0.08 * ease_out(st["reveal_p"] / 0.5)
                if sc != 1:
                    layer = layer.resize((int(box * sc), int(box * sc)), Image.BILINEAR)
                self.paste_center(img, layer, cx, cy)
            elif st["shape_p"] > 0:
                mask = self.morph_mask(art, box, ease(st["shape_p"]))
                self.paste_center(img, Image.new("RGB", (box, box), (10, 10, 14)), cx, cy, mask)
                d = ImageDraw.Draw(img)
                if st["shape_p"] < 0.5:
                    self.text(d, (cx, cy), "?", int(box * 0.45), fill=accent, alpha=1 - st["shape_p"] * 2)
            else:
                r = int(box * 0.36)
                d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(10, 10, 14))
                self.text(d, (cx, cy), "?", int(box * 0.45), fill=accent)

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
            a = ease_out(st["reveal_p"] / 0.4)
            self.text(d, (x, y), st["answer"], 104, anchor=anchor, max_width=g["text_w"], alpha=a)
            y += int(84 * self.u)
            self.text(d, (x, y), st["subtitle"], 40, fill=accent, bold=False, anchor=anchor, alpha=a)
            y += int(84 * self.u)
            for ln in self.wrap(st["fact"], 36, g["text_w"])[:8]:
                self.text(d, (x, y), ln, 36, fill=(230, 230, 238), bold=False, anchor=anchor, alpha=a)
                y += int(50 * self.u)
        else:
            if st["status"]:
                self.text(d, (x, y), st["status"], 40, fill=(200, 202, 214), bold=False, anchor=anchor)
                y += int(74 * self.u)
            for i, (hint, age) in enumerate(st["hints"]):
                a = ease_out(age / 0.45)
                dx = int((1 - a) * 60 * self.u) * (0 if g["center"] else 1)
                s = hint[0].upper() + hint[1:]
                for j, ln in enumerate(self.wrap(s, 40, g["text_w"], bold=(i == 0))[:5]):
                    self.text(d, (x + dx, y), ln, 40, bold=(i == 0), anchor=anchor, alpha=a)
                    y += int(56 * self.u)
                y += int(18 * self.u)

        # ring timer
        if st["think"] is not None:
            rx, ry, rr = g["ring"]
            w = int(16 * self.u)
            d.ellipse((rx - rr, ry - rr, rx + rr, ry + rr), outline=(55, 57, 72), width=w)
            d.arc((rx - rr, ry - rr, rx + rr, ry + rr), start=-90, end=-90 + 360 * (1 - st["think"]), fill=accent, width=w)
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
