"""Still-image generation (Pillow). Every on-screen frame is a 'card'."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..config import Settings

FONT_BOLD = "DejaVuSans-Bold.ttf"
FONT_REG = "DejaVuSans.ttf"


class Cards:
    def __init__(self, settings: Settings, accent: tuple, bg: tuple):
        self.s = settings
        self.W, self.H = settings.width, settings.height
        self.accent, self.bg = accent, bg
        self.fonts_dir = settings.assets_dir / "fonts"

    def font(self, size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self.fonts_dir / (FONT_BOLD if bold else FONT_REG)), size)

    # ---- primitives -----------------------------------------------------------
    def base(self) -> Image.Image:
        img = Image.new("RGB", (self.W, self.H), self.bg)
        d = ImageDraw.Draw(img)
        # soft radial glow in the accent colour, bottom-left, so cards aren't flat
        glow = Image.new("RGB", (self.W, self.H), self.bg)
        gd = ImageDraw.Draw(glow)
        r = int(self.H * 0.9)
        gd.ellipse((-r // 2, self.H - r // 2, r // 2, self.H + r // 2),
                   fill=tuple(int(b + (a - b) * 0.25) for a, b in zip(self.accent, self.bg)))
        glow = glow.filter(ImageFilter.GaussianBlur(220))
        img = Image.blend(img, glow, 0.9)
        d = ImageDraw.Draw(img)
        d.rectangle((0, self.H - 14, self.W, self.H), fill=self.accent)
        return img

    def text(self, d: ImageDraw.ImageDraw, xy, s: str, size: int, fill=(255, 255, 255),
             bold=True, anchor="mm", max_width: int | None = None) -> None:
        f = self.font(size, bold)
        if max_width:
            while f.getlength(s) > max_width and size > 24:
                size -= 4
                f = self.font(size, bold)
        d.text(xy, s, font=f, fill=fill, anchor=anchor)

    def wrap(self, s: str, size: int, max_width: int, bold=False) -> list[str]:
        f = self.font(size, bold)
        words, lines, cur = s.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if f.getlength(t) <= max_width:
                cur = t
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def fit_art(self, path: Path, box: int) -> Image.Image:
        im = Image.open(path).convert("RGBA")
        im.thumbnail((box, box), Image.LANCZOS)
        return im

    def silhouette(self, art: Image.Image) -> Image.Image:
        sil = Image.new("RGBA", art.size, (0, 0, 0, 0))
        black = Image.new("RGBA", art.size, (8, 8, 12, 255))
        sil.paste(black, mask=art.getchannel("A"))
        return sil

    def paste_center(self, img: Image.Image, layer: Image.Image, cx: int, cy: int) -> None:
        img.paste(layer, (cx - layer.width // 2, cy - layer.height // 2), layer)

    # ---- cards ----------------------------------------------------------------
    def title_card(self, title: str, subtitle: str, out: Path) -> Path:
        img = self.base()
        d = ImageDraw.Draw(img)
        lines = self.wrap(title, 110, self.W - 300, bold=True)
        y = self.H // 2 - 60 * len(lines)
        for ln in lines:
            self.text(d, (self.W // 2, y), ln, 110)
            y += 130
        self.text(d, (self.W // 2, y + 20), subtitle, 54, fill=self.accent, bold=False)
        img.save(out)
        return out

    def question_card(self, n: int, total: int, prompt: str, art: Path | None, out: Path,
                      silhouette: bool, progress: float | None = None, seconds_left: int | None = None) -> Path:
        """progress: None = no timer; 0..1 = fraction of countdown elapsed."""
        img = self.base()
        d = ImageDraw.Draw(img)
        self.text(d, (120, 90), f"ROUND {n} / {total}", 44, fill=self.accent, anchor="lm")
        self.text(d, (self.W - 120, 90), prompt, 44, fill=(200, 200, 210), anchor="rm", bold=False)

        cx = self.W // 2 if art is None else int(self.W * 0.62)
        if art:
            layer = self.fit_art(art, 760)
            if silhouette:
                layer = self.silhouette(layer)
            self.paste_center(img, layer, cx, self.H // 2 + 20)
            d = ImageDraw.Draw(img)
            self.text(d, (cx, self.H // 2 + 20), "?", 420, fill=self.accent)

        # timer ring, left side
        rx, ry, rr = int(self.W * 0.22), self.H // 2 + 20, 230
        if progress is not None:
            d.ellipse((rx - rr, ry - rr, rx + rr, ry + rr), outline=(60, 60, 80), width=22)
            d.arc((rx - rr, ry - rr, rx + rr, ry + rr), start=-90,
                  end=-90 + 360 * (1 - progress), fill=self.accent, width=22)
            self.text(d, (rx, ry), str(seconds_left), 240)
        else:
            # pre-countdown: a pulsing speaker glyph stand-in
            d.ellipse((rx - rr, ry - rr, rx + rr, ry + rr), outline=self.accent, width=22)
            self.text(d, (rx, ry - 10), "♪", 260, fill=self.accent)
            self.text(d, (rx, ry + 150), "LISTEN", 48, fill=(200, 200, 210))
        img.save(out)
        return out

    def reveal_card(self, n: int, total: int, answer: str, subtitle: str, fact: str,
                    art: Path | None, out: Path) -> Path:
        img = self.base()
        d = ImageDraw.Draw(img)
        self.text(d, (120, 90), f"ROUND {n} / {total}", 44, fill=self.accent, anchor="lm")
        cx = int(self.W * 0.72)
        if art:
            layer = self.fit_art(art, 700)
            self.paste_center(img, layer, cx, self.H // 2 - 40)
            d = ImageDraw.Draw(img)
        lx = 120
        self.text(d, (lx, 300), answer, 120, anchor="lm", max_width=int(self.W * 0.5) - 60)
        self.text(d, (lx, 390), subtitle, 44, fill=self.accent, anchor="lm", bold=False)
        y = 500
        for ln in self.wrap(fact, 40, int(self.W * 0.5) - 60)[:9]:
            self.text(d, (lx, y), ln, 40, fill=(225, 225, 235), bold=False, anchor="lm")
            y += 56
        img.save(out)
        return out

    def thumbnail(self, title: str, art: Path | None, out: Path, silhouette=True) -> Path:
        img = self.base().resize((1280, 720), Image.LANCZOS)
        d = ImageDraw.Draw(img)
        if art:
            layer = self.fit_art(art, 560)
            if silhouette:
                layer = self.silhouette(layer)
            self.paste_center(img, layer, 930, 380)
            d = ImageDraw.Draw(img)
            self.text(d, (930, 380), "?", 300, fill=self.accent)
        y = 200
        for ln in self.wrap(title, 96, 640, bold=True)[:3]:
            # outline for legibility at small sizes
            for dx, dy in ((-4, 0), (4, 0), (0, -4), (0, 4)):
                self.text(d, (60 + dx, y + dy), ln, 96, fill=(0, 0, 0), anchor="lm")
            self.text(d, (60, y), ln, 96, anchor="lm")
            y += 110
        img.save(out, quality=92)
        return out
