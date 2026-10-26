"""Персонажи, нарисованные кодом: Клоп Александрович (ведущий) и Клопик (корреспондентка с розовым бантиком).

Рисуем в 3× и уменьшаем — так края гладкие. Позы: рот (0 закрыт, 1 приоткрыт, 2 открыт), моргание,
наклон усиков (-1, 0, 1). Спрайты кэшируются: на сюжет их нужно не больше пары десятков.
"""
import math
from functools import lru_cache

from PIL import Image, ImageDraw

SS = 3
W, H = 440, 470
RED, BLACK, WHITE = (226, 54, 54), (22, 22, 26), (255, 255, 255)
CHEEK, MOUTH, TONGUE = (240, 120, 140), (120, 20, 30), (240, 110, 120)
TIE, PINK, PINK_DARK, MIC = (59, 110, 230), (255, 95, 162), (214, 60, 125), (60, 64, 72)


class Pen:
    """ImageDraw в координатах итогового спрайта."""

    def __init__(self, img: Image.Image):
        self.d = ImageDraw.Draw(img)

    def ellipse(self, cx, cy, rx, ry, fill, outline=None, width=0):
        self.d.ellipse([(cx - rx) * SS, (cy - ry) * SS, (cx + rx) * SS, (cy + ry) * SS],
                       fill=fill, outline=outline, width=width * SS)

    def circle(self, cx, cy, r, fill):
        self.ellipse(cx, cy, r, r, fill)

    def line(self, pts, fill, width):
        self.d.line([(x * SS, y * SS) for x, y in pts], fill=fill, width=width * SS, joint="curve")
        for x, y in (pts[0], pts[-1]):  # скруглённые концы
            self.circle(x, y, width / 2, fill)

    def polygon(self, pts, fill):
        self.d.polygon([(x * SS, y * SS) for x, y in pts], fill=fill)

    def pieslice(self, cx, cy, rx, ry, start, end, fill):
        self.d.pieslice([(cx - rx) * SS, (cy - ry) * SS, (cx + rx) * SS, (cy + ry) * SS], start, end, fill=fill)

    def arc(self, cx, cy, rx, ry, start, end, fill, width):
        self.d.arc([(cx - rx) * SS, (cy - ry) * SS, (cx + rx) * SS, (cy + ry) * SS], start, end,
                   fill=fill, width=width * SS)


def _body(p: Pen, cx: int, top: int):
    p.ellipse(cx, top + 150, 175, 160, RED)
    p.line([(cx, top + 10), (cx, top + 320)], BLACK, 6)  # шов надкрылий
    for dx, dy, r in ((-95, 70, 26), (95, 70, 26), (-130, 150, 22), (130, 150, 22), (-60, 165, 24), (60, 165, 24)):
        p.circle(cx + dx, top + dy, r, BLACK)


def _antennae(p: Pen, cx: int, head_y: int, r: int, sway: int):
    for side in (-1, 1):
        base = (cx + side * r * 0.35, head_y - r * 0.85)
        tip = (cx + side * (r * 0.75) + sway * 10, head_y - r - 40)
        mid = ((base[0] + tip[0]) / 2 + side * 6, (base[1] + tip[1]) / 2)
        p.line([base, mid, tip], BLACK, 6)
        p.circle(*tip, 11, BLACK)


def _face(p: Pen, cx: int, cy: int, r: int, mouth: int, blink: bool, lashes: bool):
    p.circle(cx, cy, r, BLACK)
    ex, ey, er = r * 0.4, cy - r * 0.12, r * 0.29
    for side in (-1, 1):
        x = cx + side * ex
        if blink:
            p.arc(x, ey, er * 0.9, er * 0.55, 20, 160, WHITE, 5)
        else:
            p.circle(x, ey, er, WHITE)
            p.circle(x + side * 2, ey + 3, er * 0.42, BLACK)
            p.circle(x + side * 2 - 4, ey - 3, er * 0.13, WHITE)
        if lashes:
            for k in (-1, 0, 1):
                p.line([(x + side * (er * 0.6 + k * 8), ey - er * 0.8),
                        (x + side * (er * 0.85 + k * 12), ey - er * 1.25)], WHITE, 3)
        else:  # брови: ведущий слегка ироничен — одна бровь выше
            lift = 8 if side == 1 else 0
            p.line([(x - er * 0.8, ey - er * 1.25 - lift), (x + er * 0.8, ey - er * 1.35 - lift)], WHITE, 6)
        p.ellipse(cx + side * r * 0.62, cy + r * 0.38, r * 0.15, r * 0.09, CHEEK)
    my, mw = cy + r * 0.5, r * 0.32
    if mouth == 0:
        p.arc(cx, my - 10, mw, 16, 25, 155, WHITE, 5)
    else:
        mh = 11 if mouth == 1 else 22
        p.ellipse(cx, my, mw * (0.8 if mouth == 1 else 0.9), mh, MOUTH)
        p.ellipse(cx, my + mh * 0.45, mw * 0.5, mh * 0.45, TONGUE)
        p.ellipse(cx, my - mh * 0.75, mw * 0.55, mh * 0.22, WHITE)  # зубы


def costume(p: Pen, kind: str, cx: int, cy: int, r: int):
    """Праздничный наряд поверх головы (центр cx, cy, радиус r). Всё влезает в спрайт: колпаки заломлены набок."""
    if kind == "santa":
        p.polygon([(cx - r * 0.9, cy - r * 0.6), (cx - r * 0.35, cy - r * 1.08), (cx + r * 0.4, cy - r * 1.1),
                   (cx + r * 1.15, cy - r * 0.5), (cx + r * 0.9, cy - r * 0.6)], (210, 30, 40))
        p.ellipse(cx, cy - r * 0.66, r * 0.98, r * 0.14, WHITE)
        p.circle(cx + r * 1.15, cy - r * 0.5, r * 0.16, WHITE)
    elif kind == "witch":
        hat, band = (55, 30, 80), (249, 115, 22)
        p.polygon([(cx - r * 0.6, cy - r * 0.72), (cx - r * 0.2, cy - r * 1.22), (cx + r * 0.85, cy - r * 1.45),
                   (cx + r * 0.25, cy - r * 1.1), (cx + r * 0.6, cy - r * 0.72)], hat)  # конус с заломленной макушкой
        p.polygon([(cx - r * 0.56, cy - r * 0.92), (cx + r * 0.53, cy - r * 0.92), (cx + r * 0.58, cy - r * 0.78),
                   (cx - r * 0.59, cy - r * 0.78)], band)
        p.ellipse(cx, cy - r * 0.72, r * 1.2, r * 0.16, hat)
    elif kind == "party":
        p.polygon([(cx - r * 0.4, cy - r * 0.85), (cx + r * 0.4, cy - r * 0.85), (cx + r * 0.12, cy - r * 1.42)],
                  (250, 204, 21))
        for t, c in ((0.25, PINK), (0.55, TIE)):  # полоски
            y, half = cy - r * (0.85 + 0.57 * t), r * 0.4 * (1 - t) + 2
            p.polygon([(cx - half + r * 0.12 * t, y), (cx + half + r * 0.12 * t, y),
                       (cx + half * 0.8 + r * 0.12 * t, y - r * 0.08), (cx - half * 0.8 + r * 0.12 * t, y - r * 0.08)], c)
        p.circle(cx + r * 0.12, cy - r * 1.42, r * 0.09, (226, 54, 54))
    elif kind == "bunny":
        for side in (-1, 1):
            p.ellipse(cx + side * r * 0.38, cy - r * 1.12, r * 0.17, r * 0.34, WHITE)
            p.ellipse(cx + side * r * 0.38, cy - r * 1.1, r * 0.08, r * 0.24, (255, 170, 200))
    elif kind == "flower":
        fx, fy = cx - r * 0.72, cy - r * 0.72
        for k in range(5):
            a = k * 2 * math.pi / 5
            p.circle(fx + math.cos(a) * r * 0.13, fy + math.sin(a) * r * 0.13, r * 0.12, (255, 120, 170))
        p.circle(fx, fy, r * 0.08, (250, 204, 21))


UMBRELLA, MITTEN, BEANIE = ((250, 204, 21), (59, 130, 246)), (59, 130, 246), (59, 130, 246)


def weather_gear(p: Pen, gear: str, cx: int, cy: int, r: int, hat: bool, stage: str):
    """Наряд по погоде. stage="back" — зонтик (за персонажем), "front" — очки, шапка, варежки поверх."""
    items = set(gear.split("+")) if gear else set()
    if stage == "back" and "umbrella" in items:
        ux, uy = cx - 70, cy - r * 1.0  # купол над головой, ручка — в левую лапку
        p.line([(ux, uy), (cx - 128, 445)], (60, 64, 72), 7)
        for k in range(6):  # дольки купола в два цвета
            p.pieslice(ux, uy + 30, 150, 75, 180 + k * 30, 210 + k * 30, UMBRELLA[k % 2])
        p.circle(ux, uy - 46, 7, (60, 64, 72))
        p.arc(cx - 140, 448, 14, 14, 0, 180, (60, 64, 72), 6)  # крючок ручки
    if stage != "front":
        return
    if "sunglasses" in items:
        ex, ey, er = r * 0.4, cy - r * 0.12, r * 0.29
        for side in (-1, 1):
            p.ellipse(cx + side * ex, ey, er * 1.3, er * 1.05, (60, 30, 70), outline=PINK, width=6)
            p.arc(cx + side * ex - 4, ey - 2, er * 0.75, er * 0.6, 200, 270, WHITE, 5)  # блик
        p.line([(cx - ex + er * 1.25, ey - 4), (cx + ex - er * 1.25, ey - 4)], PINK, 6)
    if "winter" in items:
        if not hat:  # праздничный колпак главнее
            p.pieslice(cx, cy - r * 0.55, r * 0.95, r * 0.6, 180, 360, BEANIE)
            p.ellipse(cx, cy - r * 0.58, r * 0.98, r * 0.13, WHITE)
            p.circle(cx, cy - r * 1.18, r * 0.16, WHITE)
        for mx in (cx - 128, cx + 128):  # варежки на лапках
            p.ellipse(mx, 438, 26, 22, MITTEN)
            p.ellipse(mx + (14 if mx > cx else -14), 426, 9, 12, MITTEN)
            p.ellipse(mx, 452, 26, 6, WHITE)


@lru_cache(maxsize=64)
def anchor(mouth: int = 0, blink: bool = False, sway: int = 0, dress: str = "") -> Image.Image:
    """Клоп Александрович: божья коровка в белой рубашке и синем галстуке."""
    img = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    p, cx = Pen(img), W // 2
    _body(p, cx, 290)
    p.polygon([(cx - 55, 285), (cx, 330), (cx + 55, 285), (cx + 40, 345), (cx, 360), (cx - 40, 345)], WHITE)
    p.polygon([(cx - 14, 318), (cx + 14, 318), (cx + 20, 420), (cx, 445), (cx - 20, 420)], TIE)
    p.polygon([(cx - 15, 300), (cx + 15, 300), (cx + 12, 322), (cx - 12, 322)], TIE)
    p.line([(cx - 8, 360), (cx + 10, 345)], (250, 210, 60), 3)
    _antennae(p, cx, 175, 118, sway)
    _face(p, cx, 175, 118, mouth, blink, lashes=False)
    costume(p, dress, cx, 175, 118)
    return img.resize((W, H), Image.LANCZOS)


@lru_cache(maxsize=64)
def reporter(mouth: int = 0, blink: bool = False, sway: int = 0, dress: str = "", gear: str = "") -> Image.Image:
    """Клопик: божья коровка с розовым бантиком, шарфиком и микрофоном."""
    img = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    p, cx = Pen(img), W // 2
    weather_gear(p, gear, cx, 190, 105, bool(dress), "back")
    _body(p, cx, 300)
    p.ellipse(cx, 300, 70, 22, PINK)  # шарфик
    _antennae(p, cx, 190, 105, sway)
    _face(p, cx, 190, 105, mouth, blink, lashes=True)
    costume(p, dress, cx, 190, 105)
    bx, by = cx + 62, 95  # бантик набок
    p.polygon([(bx, by), (bx - 55, by - 35), (bx - 50, by + 30)], PINK)
    p.polygon([(bx, by), (bx + 55, by - 35), (bx + 50, by + 30)], PINK)
    p.circle(bx, by, 15, PINK_DARK)
    mx, my = cx + 120, 330  # микрофон в лапке
    p.line([(mx + 15, 465), (mx, my + 20)], MIC, 14)
    p.polygon([(mx - 26, my + 12), (mx + 26, my + 12), (mx + 22, my + 52), (mx - 22, my + 52)], PINK)
    p.circle(mx, my, 28, (90, 94, 104))
    p.ellipse(cx + 128, 440, 22, 18, BLACK)  # лапка
    if "umbrella" in gear:
        p.ellipse(cx - 128, 440, 22, 18, BLACK)  # вторая лапка держит зонтик
    weather_gear(p, gear, cx, 190, 105, bool(dress), "front")
    return img.resize((W, H), Image.LANCZOS)
