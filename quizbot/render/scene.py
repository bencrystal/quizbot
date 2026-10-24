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
            if st["reveal_p"] == 0 and st["stages"] > 3:     # with 2 hints the arc notches already say it
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
            y = g["name_y"] - (int(14 * self.u) if self.portrait else 0)   # keep the block centred after the wider gaps
            # landscape: bigger type, plus the Pokédex entry under the one-liner so the column isn't empty
            name_sz, fact_sz, entry_sz = (104, 36, 0) if self.portrait else (124, 44, 32)
            fact_lines = self.wrap(st["fact"], fact_sz, g["text_w"])[:4]
            if st.get("aka"):
                fact_lines += self.wrap("Also known as " + ", ".join(st["aka"]) + ".", fact_sz, g["text_w"])[:2]
            entry_lines = self.wrap(st["entry"], entry_sz, g["text_w"])[:6] if entry_sz and st["entry"] else []
            if not self.portrait:
                block = (int(100 * self.u) + int(100 * self.u) + len(fact_lines) * int(fact_sz * 1.4 * self.u)
                         + (int(30 * self.u) + len(entry_lines) * int(entry_sz * 1.45 * self.u) if entry_lines else 0))
                y = g["art"][1] - block // 2 + int(30 * self.u)
            a = ease_out(st["reveal_p"] / 0.4)
            rx_, ranchor = (g["art"][0], "mm") if self.portrait else (x, anchor)
            self.text(d, (rx_, y), st["answer"], name_sz, anchor=ranchor, max_width=g["text_w"], alpha=a)
            y += int((112 if self.portrait else 100) * self.u)      # clear the name's descenders
            label = st["subtitle"]
            f = self.font(34)
            w = f.getlength(label) + int(44 * self.u)
            h = int(58 * self.u)
            px = rx_ - w / 2 if ranchor == "mm" else rx_
            d.rounded_rectangle((px, y - h // 2, px + w, y + h // 2), radius=h // 2,
                                fill=tuple(int(c * a) for c in accent))
            self.text(d, (px + w / 2, y), label, 34, fill=bg, alpha=a)
            y += int((98 if self.portrait else 100) * self.u)
            for ln in fact_lines:
                self.text(d, (rx_, y), ln, fact_sz, fill=(230, 230, 238), bold=False, anchor=ranchor, alpha=a)
                y += int(fact_sz * 1.4 * self.u)
            if entry_lines:
                y += int(30 * self.u)
                for ln in entry_lines:
                    self.text(d, (rx_, y), ln, entry_sz, fill=(175, 178, 196), bold=False, anchor=ranchor, alpha=a)
                    y += int(entry_sz * 1.45 * self.u)
            if self.portrait and st["caption"]:
                # snappy Shorts: the score prompt as a line under the fact instead of a spoken outro
                y += int(26 * self.u)
                for ln in self.wrap_fit(st["caption"], 40, g["text_w"], 2, bold=True)[0]:
                    self.text(d, (rx_, y), ln, 40, fill=accent, anchor="mm", alpha=a)
                    y += int(50 * self.u)
        else:
            title_on = self.portrait and not st["hints"] and st.get("title")
            if title_on:
                cx, ty = g["art"][0], int(self.H * 0.64)
                for ln in self.wrap_fit(st["title"], 72, g["text_w"], 2, bold=True)[0]:
                    self.text(d, (cx, ty), ln, 72, fill=accent, anchor="mm")
                    ty += int(84 * self.u)
                ty += int(6 * self.u)
                for ln in self.wrap_fit(st["rule"], 36, g["text_w"], 2, bold=False)[0]:
                    self.text(d, (cx, ty), ln, 36, fill=(200, 202, 214), bold=False, anchor="mm")
                    ty += int(46 * self.u)
            if st["caption"] and not st["hints"]:
                if title_on:          # the spoken opening line sits in the status slot, above the title block
                    cy = g["wave"][1] + int(60 * self.u)
                    for ln in self.wrap_fit(st["caption"], 44, g["text_w"], 2, bold=True)[0]:
                        self.text(d, (g["art"][0], cy), ln, 44, anchor="mm")
                        cy += int(54 * self.u)
                else:
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
            hints = st["hints"]
            latest = len(hints) - 1
            # Transition: when a new hint lands, the previous hint shrinks from its full layout into its
            # collapsed one-liner and everything above it settles into place — one eased motion, not a snap.
            T_IN = 0.45
            p_new = ease_out(hints[-1][1] / T_IN) if hints else 1.0

            def layout(items, collapse_last):
                """[(lines, size, bold)] for a list of hints where all but the last are collapsed,
                or all collapsed when collapse_last=True."""
                out = []
                for k, (hint, _) in enumerate(items):
                    s_ = hint[0].upper() + hint[1:]
                    full = (k == len(items) - 1) and not collapse_last
                    if full:
                        lines, size = self.wrap_fit(s_, 40, g["text_w"], max_lines=7, bold=True)
                    else:
                        lines, size = self.wrap_fit(s_, 32, g["text_w"], max_lines=1, bold=False)
                        if lines and not s_.endswith(lines[-1]):
                            lines[-1] = lines[-1].rstrip(",.;: ") + "…"
                    out.append((lines, size, full))
                return out

            def ys(lay, y0):
                """Top y of each block, centred on the disc in landscape."""
                total_h = sum(len(l) * int(sz * 1.4 * self.u) + int(16 * self.u) for l, sz, _ in lay)
                yy = max(int(self.H * 0.14), g["art"][1] - total_h // 2) if not self.portrait else y0
                pos = []
                for l, sz, _ in lay:
                    pos.append(yy)
                    yy += len(l) * int(sz * 1.4 * self.u) + int(16 * self.u)
                return pos

            new_lay = layout(hints, False)
            new_y = ys(new_lay, y)
            if len(hints) >= 2 and p_new < 1:
                old_lay = layout(hints[:-1], False)       # previous hint was the full one
                old_y = ys(old_lay, y)
            else:
                old_lay, old_y = new_lay[:-1], new_y[:-1]

            for i, (lines, size, full) in enumerate(new_lay):
                if i == latest:
                    a = p_new
                    dx = int((1 - p_new) * 60 * self.u)
                    yy = new_y[i]
                    for ln in lines:
                        self.text(d, (x + dx, yy), ln, size, bold=True, anchor=anchor, alpha=a)
                        yy += int(size * 1.4 * self.u)
                    continue
                if i == latest - 1 and p_new < 1:
                    # the hint that was just the headline: crossfade full → collapsed while sliding up
                    ol, osz, _ = old_lay[i]
                    yy_old, yy_new = old_y[i], new_y[i]
                    yy = int(yy_old + (yy_new - yy_old) * p_new)
                    if p_new < 0.5:
                        a = 1.0 - p_new * 2 * 0.45
                        for ln in ol:
                            self.text(d, (x, yy), ln, osz, bold=True, anchor=anchor, alpha=a * (1 - p_new * 2))
                            yy += int(osz * 1.4 * self.u)
                    else:
                        for ln in lines:
                            self.text(d, (x, yy), ln, size, bold=False, anchor=anchor, alpha=0.55 * ((p_new - 0.5) * 2))
                            yy += int(size * 1.4 * self.u)
                    continue
                # older collapsed hints: slide from their old slot to the new one
                yy_old = old_y[i] if i < len(old_y) else new_y[i]
                yy = int(yy_old + (new_y[i] - yy_old) * p_new)
                for ln in lines:
                    self.text(d, (x, yy), ln, size, bold=False, anchor=anchor, alpha=0.55)
                    yy += int(size * 1.4 * self.u)

        return img

    # ---- thumbnails -------------------------------------------------------------
    # What works on YouTube thumbnails (and survives at 120 px wide): one big subject, 2-4 words of text, hard
    # contrast, the brand tint. Two styles, A/B-tested on long-form (experiment `thumb`):
    #   sil  - black silhouettes of the episode's best-known items with a glowing "?" (no spoilers, classic
    #          "who's that" tension)
    #   art  - the same items in full colour (spoils three answers, but colour and faces pull clicks)
    def thumbnail(self, items: list, theme: str, out: Path, style: str = "sil", short: bool = False,
                  rounds: int = 1, tint: tuple | None = None) -> Path:
        saved = (self.W, self.H, self.portrait, self.u, self._glow)
        self.W, self.H = (1080, 1920) if short else (1280, 720)
        self.portrait, self.u, self._glow = short, (1 if short else 720 / 1080), None
        self._fonts.clear()
        accent = tint or (items[0].tint if items and items[0].tint else None) or NEUTRAL_ACCENT
        bg = lerp_rgb(NEUTRAL_BG, accent, 0.18)
        img = self.background(bg, accent, 7)
        d = ImageDraw.Draw(img)
        arts = [it for it in items if it.reveal_image and Path(it.reveal_image).exists()]
        if short:
            # one big subject high in the frame (the Shorts shelf crops to a 9:16 tile; keep the lower third free)
            it = arts[0] if arts else None
            box = int(self.W * 0.78)
            cx, cy = self.W // 2, int(self.H * 0.40)
            self._thumb_subject(img, d, it, box, cx, cy, style, accent, getattr(it, "shape_hint", True) if it else True)
            kicker = "CAN YOU NAME IT?"
            lines = ["GUESS", "THE SOUND"]
            y = int(self.H * 0.70)
            for ln in lines:
                self._outlined(d, (self.W // 2, y), ln, 150, accent if ln == "THE SOUND" else WHITE)
                y += 160
            self._outlined(d, (self.W // 2, int(self.H * 0.62)), kicker, 56, WHITE, bold=True)
        else:
            # three subjects fanned on the right, the theme huge on the left
            picks = arts[:3]
            slots = [(int(self.W * 0.67), int(self.H * 0.50), 370), (int(self.W * 0.86), int(self.H * 0.33), 320),
                     (int(self.W * 0.83), int(self.H * 0.73), 280)]
            for it, (cx, cy, box) in zip(picks, slots):
                self._thumb_subject(img, d, it, box, cx, cy, style, accent, getattr(it, "shape_hint", True))
            if not picks:
                self._thumb_subject(img, d, None, 360, int(self.W * 0.76), int(self.H * 0.50), style, accent, True)
            label = theme.replace("-type Pokémon", " TYPE").replace("Generation", "GEN").upper()
            lines, size = self.wrap_fit(label, 150, int(self.W * 0.56), 2, bold=True, min_size=90)
            y = int(self.H * 0.45) - (len(lines) - 1) * int(size * 0.5)
            for ln in lines:
                self._outlined(d, (56, y), ln, size, WHITE, anchor="lm")
                y += int(size * 0.98)
            self._outlined(d, (56, int(self.H * 0.17)), "GUESS THE SOUND", 56, accent, anchor="lm")
            if rounds > 1:
                pill = f"{rounds} ROUNDS"
                f = self.font(44, True)
                w = f.getlength(pill) + 48
                x0, y0 = 56, int(self.H * 0.80)
                d.rounded_rectangle((x0, y0, x0 + w, y0 + 70), radius=35, fill=accent)
                d.text((x0 + w / 2, y0 + 35), pill, font=f, fill=(12, 12, 16), anchor="mm")
        img.save(out, quality=92)
        self.W, self.H, self.portrait, self.u, self._glow = saved
        self._fonts.clear()
        return out

    def _outlined(self, d, xy, s, size, fill, anchor="mm", bold=True):
        o = max(3, int(size * 0.045))
        for dx, dy in ((-o, 0), (o, 0), (0, -o), (0, o), (-o, -o), (o, o), (-o, o), (o, -o)):
            self.text(d, (xy[0] + dx, xy[1] + dy), s, size, fill=(0, 0, 0), anchor=anchor, bold=bold)
        self.text(d, xy, s, size, fill=fill, anchor=anchor, bold=bold)

    def _thumb_subject(self, img, d, it, box, cx, cy, style, accent, can_silhouette):
        """A disc with the item's art (colour) or silhouette + '?' (sil). Photos (no silhouette) blur instead."""
        r = box // 2 + 8
        # silhouettes read best as black on the bright tint (the classic "who's that" card); colour art on dark
        sil = style != "art" and (it is None or can_silhouette)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=accent if sil else (10, 10, 14))
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=WHITE if sil else accent, width=max(4, box // 60))
        if it is None:
            self._outlined(d, (cx, cy), "?", int(box * 0.9), WHITE)
            return
        if style == "art" or not can_silhouette and style != "sil":
            layer = self.art(Path(it.reveal_image), box, "color")
            self.paste_center(img, layer, cx, cy)
        elif not can_silhouette:                      # photo: heavy blur keeps the colour, hides the bird
            layer = self.art(Path(it.reveal_image), box, "color").filter(ImageFilter.GaussianBlur(box * 0.06))
            self.paste_center(img, layer, cx, cy)
            self._outlined(d, (cx, cy), "?", int(box * 0.8), accent)
        else:
            layer = self.art(Path(it.reveal_image), box, "sil")
            self.paste_center(img, layer, cx, cy)
            self._outlined(d, (cx, cy), "?", int(box * 0.42), WHITE)
