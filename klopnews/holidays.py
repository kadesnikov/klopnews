"""Праздничное оформление: костюмы персонажей, украшения студии и поздравление в интро.

Праздник дня — общий для всех (сюжеты рендерятся один раз на всех), день рождения — только в личном интро.
Чтобы добавить праздник, допиши строку в HOLIDAYS: даты, костюм, украшения и фраза для интро.
"""
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Theme:
    id: str
    title: str  # плашка в шапке: «ХЕЛЛОУИН»
    greeting: str = ""  # фраза ведущего в интро
    costume: str = ""  # santa | witch | party | bunny | flower — см. characters.costume
    decor: str = ""  # snow | bats | hearts | confetti | eggs | flowers | stars — см. studio._decor
    palette: tuple = ((255, 255, 255),)
    accent: str = "#e23636"


def easter(year: int) -> date:
    """Католическая Пасха (алгоритм Мееуса — Джонса — Батчера)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    return date(year, (h + l - 7 * m + 114) // 31, (h + l - 7 * m + 114) % 31 + 1)


def orthodox_easter(year: int) -> date:
    """Православная Пасха: по юлианскому календарю, +13 дней (верно для 1900–2099)."""
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    return date(year, (d + e + 114) // 31, (d + e + 114) % 31 + 1) + timedelta(days=13)


SNOW = ((255, 255, 255), (200, 225, 255))
CONFETTI = ((250, 204, 21), (255, 95, 162), (59, 130, 246), (34, 197, 94), (226, 54, 54))
SPRING = ((255, 95, 162), (250, 204, 21), (167, 139, 250), (255, 140, 90))


def _fixed(month: int, *days: int):
    return lambda y: {date(y, month, d) for d in days}


HOLIDAYS: list[tuple[Theme, callable]] = [
    (Theme("christmas", "РОЖДЕСТВО", "В Чехии Рождество — Ваноце. Весёлого Рождества и тёплых праздников!",
           "santa", "snow", SNOW, "#16a34a"), _fixed(12, 24, 25, 26)),
    (Theme("new_year", "НОВЫЙ ГОД", "С наступающим и с Новым годом! Пусть он будет добрее прошлого.",
           "santa", "snow", SNOW, "#e23636"), lambda y: {date(y, 12, 31), date(y, 1, 1), date(y, 1, 2)}),
    (Theme("orthodox_christmas", "РОЖДЕСТВО", "Сегодня православное Рождество. С праздником всех, кто его отмечает!",
           "santa", "snow", SNOW, "#16a34a"), _fixed(1, 7)),
    (Theme("mikulas", "МИКУЛАШ", "В Чехии сегодня вечер Микулаша: по улицам ходят Микулаш, ангел и чёрт, "
           "а детям за стишок дарят сладости.", "", "stars", ((250, 204, 21), (255, 255, 255)), "#e23636"),
     _fixed(12, 5)),
    (Theme("valentine", "14 ФЕВРАЛЯ", "Сегодня День святого Валентина. Обнимите тех, кого любите.",
           "", "hearts", ((255, 95, 162), (226, 54, 54)), "#ff5fa2"), _fixed(2, 14)),
    (Theme("march8", "8 МАРТА", "С Восьмым марта! Поздравляем всех женщин и девочек — вы лучшие.",
           "flower", "flowers", SPRING, "#ff5fa2"), _fixed(3, 8)),
    (Theme("april_fools", "1 АПРЕЛЯ", "Сегодня первое апреля, но все новости в выпуске настоящие. Честное клопье.",
           "party", "confetti", CONFETTI, "#f59e0b"), _fixed(4, 1)),
    (Theme("cosmonautics", "ДЕНЬ КОСМОНАВТИКИ", "Сегодня День космонавтики: в этот день Гагарин полетел в космос.",
           "", "stars", ((250, 204, 21), (255, 255, 255)), "#3b82f6"), _fixed(4, 12)),
    (Theme("easter", "ПАСХА", "Сегодня Пасха — в Чехии Великоноце. С праздником!",
           "bunny", "eggs", SPRING, "#10b981"), lambda y: {easter(y), easter(y) + timedelta(days=1)}),
    (Theme("orthodox_easter", "ПАСХА", "Сегодня православная Пасха. Христос воскресе!",
           "bunny", "eggs", SPRING, "#10b981"), lambda y: {orthodox_easter(y)}),
    # дни памяти — без костюмов и шуток
    (Theme("victory_cz", "8 МАЯ", "В Чехии сегодня День Победы, а завтра его отмечают в России. "
           "Помним всех, кто остановил ту войну.", "", "flowers", ((226, 54, 54),), "#e23636"), _fixed(5, 8)),
    (Theme("victory", "9 МАЯ", "Сегодня День Победы. Помним всех, кто остановил ту войну.",
           "", "flowers", ((226, 54, 54),), "#e23636"), _fixed(5, 9)),
    (Theme("school", "1 СЕНТЯБРЯ", "Сегодня первое сентября. Удачного учебного года всем школьникам и студентам!",
           "", "flowers", SPRING, "#3b82f6"), _fixed(9, 1)),
    (Theme("czech_day", "28 ОКТЯБРЯ", "Сегодня в Чехии государственный праздник — день основания Чехословакии.",
           "", "confetti", ((255, 255, 255), (226, 54, 54), (17, 69, 126)), "#11457e"), _fixed(10, 28)),
    (Theme("halloween", "ХЕЛЛОУИН", "Сегодня Хеллоуин! Мы в студии нарядились — не пугайтесь.",
           "witch", "bats", ((255, 140, 30), (30, 20, 40)), "#f97316"), _fixed(10, 31)),
]

BIRTHDAY = Theme("birthday", "ДЕНЬ РОЖДЕНИЯ", "", "party", "confetti", CONFETTI, "#ff5fa2")


def theme_for(d: date) -> Theme | None:
    for theme, dates in HOLIDAYS:
        if d in dates(d.year):
            return theme
    return None


def is_birthday(birthday: str | None, d: date) -> bool:
    """birthday — YYYY-MM-DD из настроек; у родившихся 29 февраля в невисокосный год — 28-го."""
    try:
        b = date.fromisoformat(birthday or "")
    except ValueError:
        return False
    if (b.month, b.day) == (2, 29) and not (d.year % 4 == 0 and (d.year % 100 or d.year % 400 == 0)):
        return (d.month, d.day) == (2, 28)
    return (b.month, b.day) == (d.month, d.day)
