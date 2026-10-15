"""Still-image generation (Pillow). Every on-screen frame is a 'card'. Layouts adapt to 16:9 or 9:16."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..config import Settings

FONT_BOLD = "DejaVuSans-Bold.ttf"
FONT_REG = "DejaVuSans.ttf"
WHITE = (255, 255, 255)
GREY = (200, 200, 212)
DIM = (120, 120, 140)


class Cards:
    def __init__(self, settings: Settings, accent: tuple, bg: tuple):
        self.s = settings
        self.W, self.H = settings.width, settings.height
        self.portrait = self.H > self.W
        self.accent, self.bg = accent, bg
        self.fonts_dir = settings.assets_dir / "fonts"
        self._fonts: dict = {}
        self._bg: Image.Image | None = None
        self._art_cache: dict = {}
        # scale factor so the same code looks right in both orientations
        self.u = min(self.W, self.H) / 1080

    # ---- primitives -----------------------------------------------------------
    def font(self, size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
        size = max(12, int(size * self.u))
        k = (size, bold)
        if k not in self._fonts:
            self._fonts[k] = ImageFont.truetype(str(self.fonts_dir / (FONT_BOLD if bold else FONT_REG)), size)
        return self._fonts[k]

    def base(self) -> Image.Image:
        if self._bg is None:
            img = Image.new("RGB", (self.W, self.H), self.bg)
            glow = Image.new("RGB", (self.W, self.H), self.bg)
            gd = ImageDraw.Draw(glow)
            r = int(min(self.W, self.H) * 0.9)
            gd.ellipse((-r // 2, self.H - r // 2, r // 2, self.H + r // 2),
                       fill=tuple(int(b + (a - b) * 0.25) for a, b in zip(self.accent, self.bg)))
            glow = glow.filter(ImageFilter.GaussianBlur(int(200 * self.u)))
            img = Image.blend(img, glow, 0.9)
            d = ImageDraw.Draw(img)
            d.rectangle((0, self.H - int(14 * self.u), self.W, self.H), fill=self.accent)
            self._bg = img
        return self._bg.copy()

    def text(self, d, xy, s: str, size: int, fill=WHITE, bold=True, anchor="mm", max_width=None) -> None:
        f = self.font(size, bold)
        if max_width:
            while f.getlength(s) > max_width and size > 20:
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
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def art(self, path: Path, box: int, silhouette: bool) -> Image.Image:
        k = (str(path), box, silhouette)
        if k not in self._art_cache:
            im = Image.open(path).convert("RGBA")
            im.thumbnail((box, box), Image.LANCZOS)
            if silhouette:
                sil = Image.new("RGBA", im.size, (0, 0, 0, 0))
                sil.paste(Image.new("RGBA", im.size, (8, 8, 12, 255)), mask=im.getchannel("A"))
                im = sil
            self._art_cache[k] = im
        return self._art_cache[k]

    def paste_center(self, img, layer, cx, cy) -> None:
        img.paste(layer, (int(cx - layer.width // 2), int(cy - layer.height // 2)), layer)

    def header(self, d, n: int, total: int, points: int | None) -> None:
        m = int(70 * self.u)
        self.text(d, (m, m), f"ROUND {n} / {total}" if total > 1 else self.s.channel_name.upper(),
                  40, fill=self.accent, anchor="lm")
        if points is not None:
            label = f"{points} PT{'S' if points != 1 else ''}"
            f = self.font(40)
            w = f.getlength(label) + int(50 * self.u)
            h = int(70 * self.u)
            d.rounded_rectangle((self.W - m - w, m - h // 2, self.W - m, m + h // 2),
                                radius=h // 2, fill=self.accent)
            self.text(d, (self.W - m - w / 2, m), label, 40, fill=self.bg)

    def ring(self, d, cx, cy, r, progress: float | None, seconds_left: int | None) -> None:
        w = int(20 * self.u)
        box = (cx - r, cy - r, cx + r, cy + r)
        if progress is None:
            d.ellipse(box, outline=self.accent, width=w)
            self.text(d, (cx, cy - int(8 * self.u)), "♪", int(r * 1.1), fill=self.accent)
        else:
            d.ellipse(box, outline=(60, 60, 80), width=w)
            d.arc(box, start=-90, end=-90 + 360 * (1 - progress), fill=self.accent, width=w)
            self.text(d, (cx, cy), str(seconds_left), int(r * 1.05))

    # ---- layout geometry ------------------------------------------------------
    def _geom(self):
        if self.portrait:
            return dict(art=(self.W // 2, int(self.H * 0.36)), art_box=int(self.W * 0.78),
                        text_x=int(self.W * 0.08), text_w=int(self.W * 0.84), text_y=int(self.H * 0.62),
                        ring=(self.W // 2, int(self.H * 0.86), int(110 * self.u)), center_text=True)
        return dict(art=(int(self.W * 0.72), int(self.H * 0.46)), art_box=int(self.H * 0.60),
                    text_x=int(self.W * 0.065), text_w=int(self.W * 0.46), text_y=int(self.H * 0.26),
                    ring=(int(self.W * 0.72), int(self.H * 0.86), int(90 * self.u)), center_text=False)

    # ---- cards ----------------------------------------------------------------
    def title_card(self, title: str, subtitle: str, out: Path, footer: str = "") -> Path:
        img = self.base()
        d = ImageDraw.Draw(img)
        lines = self.wrap(title, 100, self.W - int(200 * self.u), bold=True)
        y = self.H // 2 - int(60 * self.u) * len(lines)
        for ln in lines:
            self.text(d, (self.W // 2, y), ln, 100)
            y += int(120 * self.u)
        self.text(d, (self.W // 2, y + int(20 * self.u)), subtitle, 50, fill=self.accent, bold=False)
        if footer:
            self.text(d, (self.W // 2, self.H - int(120 * self.u)), footer, 36, fill=GREY, bold=False)
        img.save(out)
        return out

    def question_card(self, n: int, total: int, art: Path | None, silhouette: bool, out: Path, *,
                      points: int, hints_shown: list[str], hints_total: int, choices: list[str] | None = None,
                      progress: float | None = None, seconds_left: int | None = None, status: str = "") -> Path:
        img = self.base()
        d = ImageDraw.Draw(img)
        g = self._geom()
        self.header(d, n, total, points)
        if art:
            self.paste_center(img, self.art(art, g["art_box"], silhouette), *g["art"])
            d = ImageDraw.Draw(img)
            if silhouette:
                self.text(d, g["art"], "?", int(g["art_box"] * 0.55), fill=self.accent)

        anchor = "mm" if g["center_text"] else "lm"
        x = self.W // 2 if g["center_text"] else g["text_x"]
        y = g["text_y"]
        if status:
            self.text(d, (x, y), status, 44, fill=GREY, bold=False, anchor=anchor)
            y += int(80 * self.u)
        if choices:
            for i, c in enumerate(choices):
                self.text(d, (x, y), f"{'ABCD'[i]}   {c}", 50, anchor=anchor, max_width=g["text_w"])
                y += int(72 * self.u)
        else:
            for i in range(hints_total):
                if i < len(hints_shown):
                    s = hints_shown[i]
                    s = s[0].upper() + s[1:]
                    lines = self.wrap(s, 46, g["text_w"] - int(60 * self.u), bold=True)
                    for j, ln in enumerate(lines):
                        self.text(d, (x, y), ("● " if j == 0 else "   ") + ln if not g["center_text"] else ln,
                                  46, anchor=anchor)
                        y += int(62 * self.u)
                else:
                    self.text(d, (x, y), "○  hint " + str(i + 1), 40, fill=DIM, bold=False, anchor=anchor)
                    y += int(62 * self.u)
                y += int(14 * self.u)
        self.ring(d, *g["ring"], progress, seconds_left)
        img.save(out)
        return out

    def reveal_card(self, n: int, total: int, answer: str, subtitle: str, fact: str,
                    art: Path | None, out: Path) -> Path:
        img = self.base()
        d = ImageDraw.Draw(img)
        g = self._geom()
        self.header(d, n, total, None)
        if art:
            self.paste_center(img, self.art(art, g["art_box"], False), *g["art"])
            d = ImageDraw.Draw(img)
        if self.portrait:
            x, y, anchor, w = self.W // 2, int(self.H * 0.62), "mm", int(self.W * 0.86)
        else:
            x, y, anchor, w = g["text_x"], int(self.H * 0.28), "lm", g["text_w"]
        self.text(d, (x, y), answer, 110, anchor=anchor, max_width=w)
        y += int(90 * self.u)
        self.text(d, (x, y), subtitle, 42, fill=self.accent, bold=False, anchor=anchor)
        y += int(90 * self.u)
        for ln in self.wrap(fact, 38, w)[:8]:
            self.text(d, (x, y), ln, 38, fill=(228, 228, 238), bold=False, anchor=anchor)
            y += int(54 * self.u)
        img.save(out)
        return out

    def score_card(self, max_points: int, out: Path) -> Path:
        return self.title_card("How did you do?", f"{max_points} points possible", out,
                               footer="Drop your score in the comments")

    def thumbnail(self, title: str, art: Path | None, out: Path, silhouette=True) -> Path:
        # always 16:9 — YouTube uses it for long-form; harmless for Shorts
        saved = (self.W, self.H, self.portrait, self.u, self._bg)
        self.W, self.H, self.portrait, self.u, self._bg = 1280, 720, False, 720 / 1080, None
        img = self.base()
        d = ImageDraw.Draw(img)
        if art:
            self.paste_center(img, self.art(art, 560, silhouette), 930, 380)
            d = ImageDraw.Draw(img)
            if silhouette:
                self.text(d, (930, 380), "?", 420, fill=self.accent)
        y = 200
        for ln in self.wrap(title, 130, 640, bold=True)[:3]:
            for dx, dy in ((-4, 0), (4, 0), (0, -4), (0, 4)):
                self.text(d, (60 + dx, y + dy), ln, 130, fill=(0, 0, 0), anchor="lm")
            self.text(d, (60, y), ln, 130, anchor="lm")
            y += 110
        img.save(out, quality=92)
        self.W, self.H, self.portrait, self.u, self._bg = saved
        self._fonts.clear()
        return out
