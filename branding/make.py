"""Брендинг бота из тех же персонажей, что в студии: python branding/make.py → PNG рядом.

avatar.png      640×640 — аватар бота (Telegram обрезает кругом, важное — в центре)
avatar-klopik.png         — запасной вариант с Клопиком
cover.png       640×360 — картинка к описанию бота («Что умеет этот бот?»)
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from klopnews.render import characters
from klopnews.render.studio import RED, _logo, font

OUT = Path(__file__).parent
NAVY, NAVY2 = (11, 21, 48), (30, 48, 104)


def radial(w: int, h: int, inner, outer) -> Image.Image:
    y, x = np.ogrid[:h, :w]
    r = np.sqrt((x - w / 2) ** 2 + (y - h / 2) ** 2) / (max(w, h) / 1.4)
    r = np.clip(r, 0, 1)[..., None]
    return Image.fromarray((np.array(inner) * (1 - r) + np.array(outer) * r).astype("uint8"), "RGB")


def avatar(sprite: Image.Image, accent) -> Image.Image:
    img = radial(640, 640, NAVY2, NAVY)
    d = ImageDraw.Draw(img)
    d.ellipse([20, 20, 620, 620], outline=accent, width=14)  # кольцо «в эфире»: по краю круга Telegram
    s = sprite.resize((560, 598), Image.LANCZOS)
    img.paste(s, (40, 70), s)
    # нижний край кольца поверх тела — персонаж «в кадре»
    mask = Image.new("L", (640, 640), 0)
    ImageDraw.Draw(mask).ellipse([20, 20, 620, 620], fill=255)
    out = Image.new("RGB", (640, 640), accent)
    out.paste(img, (0, 0), mask)
    return out


def cover() -> Image.Image:
    img = radial(1280, 720, NAVY2, NAVY)
    glow = Image.new("RGB", img.size)
    gd = ImageDraw.Draw(glow)
    for x, y, r, c in [(200, 200, 120, (90, 40, 70)), (1100, 160, 100, (40, 60, 120)), (640, 650, 160, (90, 30, 50))]:
        gd.ellipse([x - r, y - r, x + r, y + r], fill=c)
    from PIL import ImageChops

    img = ImageChops.add(img, glow.filter(ImageFilter.GaussianBlur(60)))
    d = ImageDraw.Draw(img)
    a = characters.anchor(2, False, 1).resize((420, 449), Image.LANCZOS)
    k = characters.reporter(1, False, -1).resize((380, 406), Image.LANCZOS)
    img.paste(a, (20, 720 - 449), a)
    img.paste(k, (1280 - 400, 720 - 406), k)
    _logo(d, 470, 150, 44)
    f = font(44)
    for i, line in enumerate(["Твои новости —", "каждое утро"]):
        d.text((640 - f.getlength(line) / 2, 270 + i * 56), line, font=f, fill=(255, 255, 255))
    f2 = font(24, "Medium")
    for i, line in enumerate(["ведущий Клоп Александрович", "и корреспондент Клопик"]):
        d.text((640 - f2.getlength(line) / 2, 400 + i * 32), line, font=f2, fill=(190, 200, 225))
    d.rounded_rectangle([540, 490, 740, 536], radius=10, fill=RED)
    d.text((640 - font(24).getlength("● ЭФИР 07:30") / 2, 499), "● ЭФИР 07:30", font=font(24), fill=(255, 255, 255))
    return img.resize((640, 360), Image.LANCZOS)


if __name__ == "__main__":
    avatar(characters.anchor(0, False, 0), RED).save(OUT / "avatar.png")
    avatar(characters.reporter(0, False, 0), (255, 95, 162)).save(OUT / "avatar-klopik.png")
    cover().save(OUT / "cover.png")
    print("ok:", *sorted(p.name for p in OUT.glob("*.png")))
