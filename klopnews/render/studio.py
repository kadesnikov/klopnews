"""Студия «Клоп·Новости»: кадры и клипы.

Видео не рисуется покадрово. Каждые 80 мс берём громкость голоса (рот), моргание, наклон усиков и
сколько пунктов уже на экране. Уникальных состояний на сюжет — десятки, их и рисуем, а ffmpeg склеивает
по списку длительностей. Минутный сюжет рендерится за секунды.
"""
import hashlib
import io
import logging
import math
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from klopnews.markets import fmt_change, fmt_price
from klopnews.render import characters
from klopnews.tts import loudnorm, read_wav, write_wav

log = logging.getLogger(__name__)
W, H, FPS, STEP = 1280, 720, 25, 0.08
NAVY, NAVY2, PANEL, INK, MUTED = (11, 21, 48), (18, 32, 70), (15, 26, 58), (255, 255, 255), (160, 174, 205)
RED, YELLOW, GREEN = (226, 54, 54), (250, 204, 21), (34, 197, 94)
ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-g", "50", "-pix_fmt", "yuv420p", "-r", str(FPS),
          "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"]
FONT_DIRS = ["/usr/share/fonts/opentype/inter", "/usr/share/fonts/truetype/inter", "/usr/share/fonts/truetype/dejavu"]


@lru_cache(maxsize=32)
def font(size: int, weight: str = "Bold") -> ImageFont.FreeTypeFont:
    names = [f"Inter-{weight}.otf", f"Inter-{weight}.ttf", "DejaVuSans-Bold.ttf" if weight != "Regular" else "DejaVuSans.ttf"]
    for d in FONT_DIRS:
        for n in names:
            if Path(d, n).exists():
                return ImageFont.truetype(str(Path(d, n)), size)
    return ImageFont.load_default(size)


def hex_rgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))


def wrap(text: str, f: ImageFont.FreeTypeFont, width: int, max_lines: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if f.getlength(trial) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while f.getlength(lines[-1] + "…") > width and " " in lines[-1]:
            lines[-1] = lines[-1].rsplit(" ", 1)[0]
        lines[-1] += "…"
    return lines


def ellipsize(text: str, f, width: int) -> str:
    return wrap(text, f, width, 1)[0]


def fit_text(text: str, sizes: tuple[int, ...], width: int, max_lines: int, weight: str = "Bold"):
    """Самый крупный шрифт, при котором текст целиком влезает в max_lines строк. → (шрифт, строки).

    Многоточие — только если не влезло даже на самом мелком.
    """
    for size in sizes:
        f = font(size, weight)
        lines = wrap(text, f, width, 99)
        if len(lines) <= max_lines:
            return f, lines
    return f, wrap(text, f, width, max_lines)


def pill(d: ImageDraw.ImageDraw, x: int, y: int, text: str, bg, size: int = 18, fg=INK, h: int = 0) -> int:
    f = font(size)
    w, h = int(f.getlength(text)) + 24, h or size + 14
    d.rounded_rectangle([x, y, x + w, y + h], radius=6, fill=bg)
    top, bottom = f.getbbox(text)[1::2]  # по настоящим границам букв: КАПС иначе съезжает вверх
    d.text((x + 12, y + (h - top - bottom) / 2), text, font=f, fill=fg)
    return x + w


def bigger_image_url(url: str) -> str:
    """Фиды часто отдают миниатюру; у BBC и WordPress размер прямо в адресе — просим крупную."""
    url = re.sub(r"(ichef\.bbci\.co\.uk/(?:ace/)?(?:ws|standard|news)/)\d+/", r"\g<1>1024/", url)
    return re.sub(r"-\d{2,4}x\d{2,4}(\.(?:jpe?g|png|webp))", r"\1", url, flags=re.I)


def fetch_image(url: str | None, cache_dir: Path) -> Image.Image | None:
    if not url:
        return None
    big = bigger_image_url(url)
    if big != url:
        img = _download(big, cache_dir)
        if img is not None:
            return img
    return _download(url, cache_dir)


def _download(url: str, cache_dir: Path) -> Image.Image | None:
    path = cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:20] + ".jpg")
    try:
        if not path.exists():
            r = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.open(io.BytesIO(r.content)).convert("RGB").save(path, quality=90)
        img = Image.open(path).convert("RGB")
        return img if img.width >= 200 else None
    except Exception as e:
        log.info("image_failed %s: %r", url, e)
        return None


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    scale = max(w / img.width, h / img.height)
    img = img.resize((math.ceil(img.width * scale), math.ceil(img.height * scale)), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


# ── Сцены ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Scene:
    """Что показать, пока звучит одна аудиодорожка."""
    kind: str  # studio | report
    date_label: str
    tag: str = ""
    color: str = "#3b82f6"
    title: str = ""
    bullets: list[str] = field(default_factory=list)
    number: str = ""
    image: Image.Image | None = None
    image_credit: str = ""
    credit: str = ""  # источник новости — в углу карточки
    lower: str = ""  # плашка снизу; по умолчанию — заголовок
    nameplate: tuple[str, str] | None = None  # (имя, подпись) — титр персонажа
    quotes: list[dict] = field(default_factory=list)  # блок рынков
    subtitle: str = ""  # крупная вторая строка на карточке (интро/финал)
    pills: list[tuple[str, str]] = field(default_factory=list)  # [(текст, цвет)] — темы в интро
    ticker: str = ""  # бегущая строка внизу: главные заголовки дня
    theme: object = None  # holidays.Theme: костюмы, украшения и плашка праздника
    movers: list[dict] = field(default_factory=list)  # акции дня рядом с котировками


def _decor(img: Image.Image, kind: str, palette: tuple, seed: int) -> Image.Image:
    """Праздничные украшения поверх фона студии: полупрозрачные, чтобы не спорить с текстом."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d, rng = ImageDraw.Draw(layer), np.random.default_rng(seed)
    for _ in range(46):
        x, y, s = int(rng.integers(0, W)), int(rng.integers(60, 600)), int(rng.integers(8, 22))
        c = palette[int(rng.integers(len(palette)))] + (int(rng.integers(110, 200)),)
        if kind == "snow":
            d.ellipse([x - s / 3, y - s / 3, x + s / 3, y + s / 3], fill=c)
        elif kind == "hearts":
            d.ellipse([x - s, y - s, x, y], fill=c)
            d.ellipse([x, y - s, x + s, y], fill=c)
            d.polygon([(x - s, y - s / 2), (x + s, y - s / 2), (x, y + s)], fill=c)
        elif kind == "confetti":
            a = rng.uniform(0, math.pi)
            dx, dy, ex, ey = math.cos(a) * s / 2, math.sin(a) * s / 2, -math.sin(a) * s / 5, math.cos(a) * s / 5
            d.polygon([(x - dx - ex, y - dy - ey), (x + dx - ex, y + dy - ey), (x + dx + ex, y + dy + ey),
                       (x - dx + ex, y - dy + ey)], fill=c)
        elif kind == "stars":
            pts = [(x + math.cos(math.pi / 2 + k * math.pi / 5) * (s if k % 2 == 0 else s / 2.4),
                    y - math.sin(math.pi / 2 + k * math.pi / 5) * (s if k % 2 == 0 else s / 2.4)) for k in range(10)]
            d.polygon(pts, fill=c)
        elif kind == "eggs":
            d.ellipse([x - s * 0.7, y - s, x + s * 0.7, y + s], fill=c)
            d.line([x - s * 0.6, y, x + s * 0.6, y], fill=(255, 255, 255, 160), width=3)
        elif kind == "flowers":
            for k in range(5):
                a = k * 2 * math.pi / 5
                cx, cy = x + math.cos(a) * s / 2, y + math.sin(a) * s / 2
                d.ellipse([cx - s / 2.5, cy - s / 2.5, cx + s / 2.5, cy + s / 2.5], fill=c)
            d.ellipse([x - s / 4, y - s / 4, x + s / 4, y + s / 4], fill=(250, 204, 21, c[3]))
        elif kind == "bats":
            if rng.random() < 0.3:  # тыква
                d.ellipse([x - s * 1.3, y - s, x + s * 1.3, y + s], fill=(249, 115, 22, 210))
                d.rectangle([x - 2, y - s - 6, x + 2, y - s + 2], fill=(34, 120, 60, 210))
            else:  # летучая мышь
                d.polygon([(x - s * 1.6, y - s / 2), (x - s * 0.8, y), (x - s * 0.4, y - s / 3), (x, y + s / 3),
                           (x + s * 0.4, y - s / 3), (x + s * 0.8, y), (x + s * 1.6, y - s / 2), (x, y - s / 6)],
                          fill=(5, 5, 12, 200))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def _background(seed: int, theme=None) -> Image.Image:
    y = np.linspace(0, 1, H)[:, None, None]
    grad = (np.array(NAVY) * (1 - y) + np.array(NAVY2) * y) * np.ones((1, W, 1))
    img = Image.fromarray(grad.astype("uint8"), "RGB")
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd, rng = ImageDraw.Draw(glow), np.random.default_rng(seed)
    for _ in range(14):
        x, y0, r = rng.integers(0, W), rng.integers(60, 560), rng.integers(20, 70)
        c = (90, 40, 70) if rng.random() < 0.5 else (40, 60, 120)
        gd.ellipse([x - r, y0 - r, x + r, y0 + r], fill=c)
    from PIL import ImageChops

    img = ImageChops.add(img, glow.filter(ImageFilter.GaussianBlur(28)))
    return _decor(img, theme.decor, theme.palette, seed) if theme and theme.decor else img


def _logo(d: ImageDraw.ImageDraw, x: int, y: int, size: int = 26):
    r = size // 2 + 2
    d.ellipse([x, y, x + 2 * r, y + 2 * r], fill=RED)
    d.line([x + r, y + 2, x + r, y + 2 * r - 2], fill=(22, 22, 26), width=3)
    for dx, dy in ((-0.45, -0.1), (0.45, -0.1), (-0.4, 0.45), (0.4, 0.45)):
        cx, cy = x + r + dx * r, y + r + dy * r
        d.ellipse([cx - 3.5, cy - 3.5, cx + 3.5, cy + 3.5], fill=(22, 22, 26))
    f = font(size)
    d.text((x + 2 * r + 12, y + r - size * 0.62), "КЛОП", font=f, fill=INK)
    d.text((x + 2 * r + 12 + f.getlength("КЛОП"), y + r - size * 0.62), "·НОВОСТИ", font=f, fill=RED)


def _chrome(img: Image.Image, sc: Scene, live: str):
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 56], fill=(8, 14, 34))
    d.rectangle([0, 56, W, 58], fill=RED)
    _logo(d, 24, 13)
    if sc.theme:
        pill(d, 300, 15, sc.theme.title, hex_rgb(sc.theme.accent), 15)
    f = font(18, "SemiBold")
    tw = f.getlength(sc.date_label)
    d.text((W - 24 - tw, 18), sc.date_label, font=f, fill=INK)
    x = W - 40 - tw - int(font(16).getlength(live)) - 40
    d.rounded_rectangle([x, 15, x + font(16).getlength(live) + 40, 43], radius=6, fill=RED)
    d.ellipse([x + 11, 25, x + 19, 33], fill=INK)
    d.text((x + 26, 19), live, font=font(16), fill=INK)
    # нижняя плашка и полоса
    lower = sc.lower or sc.title
    if lower:
        d.rectangle([24, 606, W - 24, 652], fill=(250, 250, 252))
        x = pill(d, 24, 606, sc.tag or "НОВОСТИ", hex_rgb(sc.color), 20, h=46) if sc.tag else 24
        f, lines = fit_text(lower, (22, 20, 18, 16), W - 60 - x, 1, "SemiBold")
        top, bottom = f.getbbox(lines[0])[1::2]
        d.text((x + 16, 629 - (top + bottom) / 2), lines[0], font=f, fill=(15, 20, 35))
    d.rectangle([0, 672, W, H], fill=(8, 14, 34))
    _ticker_label(d)
    if not sc.ticker:
        d.text((TICKER_X + 22, 687), "Семейный выпуск  •  ссылки на оригиналы — под видео",
               font=font(18, "Medium"), fill=MUTED)
    if sc.nameplate:
        name, role = sc.nameplate
        x0, y0 = (40, 528) if sc.kind == "studio" else (40, 520)
        w = max(font(28).getlength(name), font(16, "Medium").getlength(role)) + 40
        d.rectangle([x0, y0, x0 + w, y0 + 70], fill=(250, 250, 252))
        d.rectangle([x0, y0, x0 + 6, y0 + 70], fill=RED)
        d.text((x0 + 20, y0 + 6), name, font=font(28), fill=(15, 20, 35))
        d.text((x0 + 20, y0 + 42), role, font=font(16, "Medium"), fill=(90, 98, 120))


TICKER_X, TICKER_Y, TICKER_SPEED = 170, 672, 90  # px/с


def _ticker_label(d: ImageDraw.ImageDraw):
    d.rectangle([0, TICKER_Y, TICKER_X, H], fill=YELLOW)
    d.text((18, TICKER_Y + 13), "ГЛАВНОЕ", font=font(20), fill=(15, 20, 35))


def _ticker_strip(text: str) -> tuple[Image.Image, int]:
    """Полоса с текстом, повторённым столько раз, чтобы при прокрутке не было пустоты. → (картинка, период)."""
    f = font(19, "Medium")
    unit = text + "     •     "
    period = int(f.getlength(unit))
    reps = 2 + (W - TICKER_X) // max(period, 1)
    strip = Image.new("RGB", (period * reps, H - TICKER_Y), (8, 14, 34))
    ImageDraw.Draw(strip).text((0, 14), unit * reps, font=f, fill=(225, 230, 242))
    return strip, period


CARD = (560, 84, W - 40, 572)


def _card(img: Image.Image, sc: Scene) -> tuple[int, int, int]:
    """Рисует карточку без пунктов; возвращает (x, y, ширина) области пунктов."""
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = CARD
    color = hex_rgb(sc.color)
    d.rounded_rectangle(CARD, radius=18, fill=PANEL, outline=color, width=3)
    d.rectangle([x0, y0 + 18, x0 + 6, y1 - 18], fill=color)
    if sc.number:
        d.text((x1 - 30 - font(64).getlength(sc.number), y0 + 18), sc.number, font=font(64), fill=(36, 56, 110))
    y = y0 + 30
    if sc.tag:
        right = pill(d, x0 + 30, y, sc.tag, color, 18)
        if sc.credit:
            f = font(16, "Medium")
            t = ellipsize(sc.credit, f, x1 - 30 - right - 40)
            tw = f.getlength(t)
            d.rounded_rectangle([x1 - 30 - tw - 20, y, x1 - 30, y + 32], radius=6, fill=(8, 14, 34))
            d.text((x1 - 40 - tw, y + 7), t, font=f, fill=MUTED)
        y += 52
    f, lines = fit_text(sc.title, (48, 42, 36) if sc.subtitle else (40, 36, 32, 28),
                        x1 - x0 - 60 - (90 if sc.number else 0), 3)
    for line in lines:
        d.text((x0 + 30, y), line, font=f, fill=INK)
        y += f.size + 10
    if sc.subtitle:
        d.text((x0 + 30, y + 6), sc.subtitle, font=font(28, "SemiBold"), fill=YELLOW)
        y += 60
    if sc.pills:
        px, py = x0 + 30, y + 20
        for text, c in sc.pills:
            w = font(22).getlength(text) + 24
            if px + w > x1 - 30:
                px, py = x0 + 30, py + 50
            pill(d, px, py, text, hex_rgb(c), 22)
            px += w + 10
    if sc.quotes:
        w = x1 - x0 - 60
        if sc.movers:
            _movers(d, sc.movers, x1 - 30 - 220, y + 14, 220)
            w -= 220 + 30
        _quotes(d, sc.quotes, x0 + 30, y + 14, w)
        return x0 + 30, y1, 0
    bw = x1 - x0 - 60
    if sc.image is not None:
        iw, ih = 250, 160
        thumb = cover(sc.image, iw, ih)
        img.paste(thumb, (x1 - 30 - iw, y1 - 30 - ih))
        bw -= iw + 24
    return x0 + 30, y + 16, bw


def _quotes(d, quotes, x, y, w):

    quotes = quotes[:12]
    row = min(48, (CARD[3] - 24 - y) // max(len(quotes), 1))  # сколько бы инструментов ни было — влезают
    size = min(row * 26 // 48, MOVERS_FONT if w < 500 else 26)
    f, fs = font(size, "SemiBold"), font(size)
    for q in quotes:
        up = q["change_pct"] >= 0
        d.text((x, y), q["name"], font=f, fill=INK)
        price = fmt_price(q)
        d.text((x + w * 0.55 - f.getlength(price), y), price, font=f, fill=INK)
        ch = ("▲ " if up else "▼ ") + fmt_change(q)
        d.text((x + w - fs.getlength(ch), y), ch, font=fs, fill=GREEN if up else RED)
        y += row


MOVERS_FONT = 19


def _movers(d, movers, x, y, w):
    """Колонка «Акции дня»: самые сильные движения из списка наблюдения."""
    d.text((x, y), "АКЦИИ ДНЯ", font=font(16), fill=MUTED)
    y += 30
    f = font(MOVERS_FONT, "SemiBold")
    for q in movers:
        up = q["change_pct"] >= 0
        ch = ("▲ " if up else "▼ ") + fmt_change(q)
        d.text((x, y), ellipsize(q["name"], f, w - f.getlength(ch) - 10), font=f, fill=INK)
        d.text((x + w - f.getlength(ch), y), ch, font=f, fill=GREEN if up else RED)
        y += 38


def fit_bullets(bullets: list[str], area: tuple[int, int, int], bottom: int) -> tuple[ImageFont.FreeTypeFont, list[list[str]]]:
    """Самый крупный шрифт, при котором все пункты целиком помещаются до bottom.

    Пункт никогда не обрезается посередине: не влезло и на мелком шрифте — последние пункты не показываем.
    """
    x, y0, w = area
    for size in (24, 22, 20, 18):
        f = font(size, "Medium")
        out, y = [], y0
        for b in bullets:
            lines = wrap(b, f, w - 28, 99)
            y += len(lines) * (size + 8) + 12
            if y > bottom:
                break
            out.append(lines)
        if len(out) == len(bullets):
            return f, out
    return f, out


def _bullets_layer(sc: Scene, area: tuple[int, int, int], fitted, k: int) -> Image.Image | None:
    f, blocks = fitted
    if not k or not area[2] or not blocks:
        return None
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, y, _ = area
    for lines in blocks[:k]:
        d.rectangle([x, y + f.size // 2 - 4, x + 10, y + f.size // 2 + 6], fill=hex_rgb(sc.color))
        for line in lines:
            d.text((x + 26, y), line, font=f, fill=INK)
            y += f.size + 8
        y += 12
    return layer


def _photo_backdrop(photo: Image.Image) -> Image.Image:
    """Фото на весь кадр. Мелкое (миниатюра из RSS) не растягиваем в мыло: размытая подложка, а поверх —
    само фото, увеличенное не больше чем вдвое, слева от Клопика."""
    if photo.width >= W * 0.8:
        return cover(photo, W, H)
    img = ImageEnhance.Brightness(cover(photo.resize((photo.width // 4 or 1, photo.height // 4 or 1)), W, H)
                                  .filter(ImageFilter.GaussianBlur(18))).enhance(0.6)
    box_w, box_h = 800, H - 58 - 48 - 60  # между шапкой и плашкой снизу
    scale = min(box_w / photo.width, box_h / photo.height, 2.0)
    fg = photo.resize((round(photo.width * scale), round(photo.height * scale)), Image.LANCZOS)
    if scale > 1:
        fg = fg.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))
    img.paste(fg, (40 + (box_w - fg.width) // 2, 58 + 30 + (box_h - fg.height) // 2))
    return img


def _report_base(sc: Scene) -> Image.Image:
    if sc.image is not None:
        img = ImageEnhance.Brightness(_photo_backdrop(sc.image)).enhance(0.78)
    else:
        img = _background(7)
        d = ImageDraw.Draw(img)
        f = font(96)
        d.text(((W - f.getlength(sc.title)) / 2 if f.getlength(sc.title) < W - 80 else 40, 240),
               ellipsize(sc.title, f, W - 80), font=f, fill=(40, 55, 100))
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)
    if sc.image_credit:
        f = font(15, "Medium")
        t = f"Фото: {sc.image_credit}"
        d.rounded_rectangle([W - 36 - f.getlength(t), 70, W - 20, 96], radius=5, fill=(0, 0, 0))
        d.text((W - 28 - f.getlength(t), 74), t, font=f, fill=(220, 220, 230))
    return img


class Frames:
    """Рисует кадр по состоянию (рот, моргание, усики, пунктов на экране) с кэшем фона."""

    def __init__(self, sc: Scene):
        self.sc = sc
        if sc.kind == "report":
            self.base, self.area, self.fitted = _report_base(sc), (0, 0, 0), (None, [])
            _chrome(self.base, sc, "ПРЯМОЕ ВКЛЮЧЕНИЕ")
        else:
            self.base = _background(len(sc.title), sc.theme)
            self.area = _card(self.base, sc)
            self.fitted = fit_bullets(sc.bullets, self.area, CARD[3] - 24) if self.area[2] else (None, [])
            d = ImageDraw.Draw(self.base)
            d.rectangle([0, 506, 540, 600], fill=(20, 32, 72))
            d.rectangle([0, 500, 540, 506], fill=RED)
            _logo(d, 150, 535, 24)
            _chrome(self.base, sc, "ЭФИР")
        self.layers: dict[int, Image.Image | None] = {}

    def render(self, mouth: int, blink: bool, sway: int, k: int) -> Image.Image:
        img = self.base.copy()
        dress = self.sc.theme.costume if self.sc.theme else ""
        if self.sc.kind == "report":
            sprite = characters.reporter(mouth, blink, sway, dress).resize((506, 540), Image.LANCZOS)
            img.paste(sprite, (W - 560, 606 - 540), sprite)
        else:
            sprite = characters.anchor(mouth, blink, sway, dress).crop((0, 0, 440, 400))
            img.paste(sprite, (60, 100), sprite)  # низ спрайта спрятан за столом (y=500)
        if k not in self.layers:
            self.layers[k] = _bullets_layer(self.sc, self.area, self.fitted, k)
        if self.layers[k] is not None:
            img.paste(self.layers[k], (0, 0), self.layers[k])
        return img


# ── Таймлайн и рендер ───────────────────────────────────────────────────────────────

def timeline(samples: np.ndarray, rate: int, n_bullets: int, seed: int = 0) -> list[tuple[tuple, float]]:
    """[(состояние, длительность)] с шагом STEP; соседние одинаковые состояния склеены."""
    hop = int(rate * STEP)
    n = max(1, math.ceil(len(samples) / hop))
    rms = np.array([np.sqrt(np.mean(samples[i * hop:(i + 1) * hop] ** 2) + 1e-12) for i in range(n)])
    loud = np.percentile(rms, 95) or 1.0
    rng = np.random.default_rng(seed)
    next_blink, out = rng.uniform(1.5, 4), []
    for i in range(n):
        t = i * STEP
        level = rms[i] / loud
        mouth = 0 if level < 0.18 else (1 if level < 0.5 else 2)
        blink = next_blink <= t < next_blink + 2 * STEP
        if t >= next_blink + 2 * STEP:
            next_blink = t + rng.uniform(2.5, 5.5)
        sway = round(math.sin(t * 2 * math.pi / 3.2))
        k = sum(t >= (0.06 + 0.8 * j / max(n_bullets, 1)) * n * STEP for j in range(n_bullets))
        state = (mouth, blink, sway, k)
        if out and out[-1][0] == state:
            out[-1] = (state, out[-1][1] + STEP)
        else:
            out.append((state, STEP))
    return out


def render_segment(sc: Scene, wav: Path, out: Path, lead: float = 0.3, tail: float = 0.5,
                   prefix: np.ndarray | None = None, suffix: np.ndarray | None = None) -> float:
    """Сцена + дорожка → mp4. prefix/suffix — джингл до и после голоса. Возвращает длительность."""
    voice, rate = loudnorm(wav)
    parts = [np.zeros(int(rate * lead), np.float32), voice, np.zeros(int(rate * tail), np.float32)]
    if prefix is not None:
        parts.insert(0, prefix)
    if suffix is not None:
        parts.append(suffix)
    samples = np.concatenate(parts)
    frames = Frames(sc)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        write_wav(tmp / "a.wav", samples, rate)
        names: dict[tuple, str] = {}
        lines = []
        for state, dur in timeline(samples, rate, len(frames.fitted[1]), seed=len(sc.title)):
            if state not in names:
                names[state] = f"f{len(names)}.png"
                frames.render(*state).save(tmp / names[state], compress_level=1)
            lines += [f"file '{names[state]}'", f"duration {dur:.3f}"]
        lines.append(lines[-2])  # concat-демуксер: последний кадр повторяется без длительности
        (tmp / "list.txt").write_text("\n".join(lines))
        out.parent.mkdir(parents=True, exist_ok=True)
        dur = len(samples) / rate
        inputs = ["-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"), "-i", str(tmp / "a.wav")]
        if sc.ticker:
            strip, period = _ticker_strip(sc.ticker)
            strip.save(tmp / "strip.png")
            lab = Image.new("RGB", (W, H))
            _ticker_label(ImageDraw.Draw(lab))
            lab.crop((0, TICKER_Y, TICKER_X, H)).save(tmp / "label.png")
            inputs += ["-loop", "1", "-i", str(tmp / "strip.png"), "-loop", "1", "-i", str(tmp / "label.png")]
            graph = (f"[0:v]fps={FPS}[b];[b][2:v]overlay=x='{TICKER_X}-mod(t*{TICKER_SPEED},{period})':y={TICKER_Y}[c];"
                     f"[c][3:v]overlay=0:{TICKER_Y}[v]")
            inputs += ["-filter_complex", graph, "-map", "[v]", "-map", "1:a"]
        ffmpeg([*inputs, *ENCODE, "-t", f"{dur:.3f}", str(out)])
    return dur


def concat(clips: list[Path], out: Path) -> float:
    """Склейка клипов с одинаковыми параметрами без перекодирования."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("\n".join(f"file '{c.resolve()}'" for c in clips))
    ffmpeg(["-f", "concat", "-safe", "0", "-i", f.name, "-c", "copy", "-movflags", "+faststart", str(out)])
    Path(f.name).unlink()
    return probe_duration(out)


def ffmpeg(args: list[str]):
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-800:]}")


def probe_duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def jingle(rate: int, up: bool = True) -> np.ndarray:
    """Три ноты колокольчика: в начале выпуска вверх, в конце вниз."""
    notes = [523.25, 659.25, 783.99] if up else [783.99, 659.25, 523.25]
    out = np.zeros(int(rate * 1.4), np.float32)
    for i, fq in enumerate(notes):
        t = np.arange(int(rate * 0.9)) / rate
        tone = (np.sin(2 * np.pi * fq * t) + 0.3 * np.sin(4 * np.pi * fq * t)) * np.exp(-t * 4) * 0.22
        s = int(rate * 0.18 * i)
        out[s:s + len(tone)] += tone[: len(out) - s]
    return out
