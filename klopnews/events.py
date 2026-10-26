"""Афиша: ИИ-ивенты в Праге — митапы, лекции, хакатоны, конференции. Перенесено из piano-piano (app/events.py).

Раз в день, перед сюжетами, читаем страницы-афиши из config/sources.yaml (events.pages) и результаты
поисковых запросов (Brave Search, нужен BRAVE_API_KEY). Модель вытаскивает из текста события на две недели
вперёд и отбрасывает всё не про ИИ. Дубли склеиваются по дате и названию. Из событий получается блок
«ИИ в Праге» в выпуске — как «Рынки»: один клип на всех, у кого включена тема ИИ.
"""
import hashlib
import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from klopnews.config import Settings
from klopnews.db import Event, EventPage
from klopnews.llm import ask_json

log = logging.getLogger(__name__)

WINDOW_DAYS = 14    # собираем на две недели вперёд
ANNOUNCE_DAYS = 7   # в выпуск — ближайшая неделя
REREAD_DAYS = 7     # страница не менялась — модель её не читает, но не дольше недели
MAX_PAGES = 15      # страниц за один сбор — столько уходит в модель, мусор отсеивается раньше
PAGE_CHARS = 12000  # текста со страницы в модель
LD_DESC = 300       # описания события из JSON-LD
BATCH = 3           # страниц в одном запросе к модели
UA = "Mozilla/5.0 (compatible; KlopNews/0.1; family news digest)"
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

EXTRACT = """You find real-life events in {city} about artificial intelligence for a curious family.
Today is {today}; time zone {tz}. Keep only events that take place in {city} between {start} and {end}
and are about AI, machine learning, data science or LLMs: meetups, talks, lectures, hackathons, conferences,
workshops, demo nights, exhibitions. Any language, free or paid.
Skip: multi-week paid courses, online-only webinars, job ads, events with unknown date.
Never invent anything that is not in the text, the start time included: if the text gives only a date, write the date alone.
Use the event's own page as url when the text gives it.
Return only JSON (starts_at is local time, exactly "YYYY-MM-DDTHH:MM" or "YYYY-MM-DD", nothing else in it):
{{"found": [{{"title": "...", "starts_at": "YYYY-MM-DDTHH:MM", "place": "venue, address",
"price": "as written, empty if unknown", "free": true|false, "lang": "en|cs|ru|...",
"summary_ru": "one short sentence in Russian: what it is and for whom", "url": "..."}}]}}"""


def tz(cfg: dict) -> ZoneInfo:
    return ZoneInfo(cfg.get("timezone") or "Europe/Prague")


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite отдаёт время без зоны


# ───────────────────────── страницы ─────────────────────────

def _ld_events(block: str) -> list[str]:
    """schema.org Event из JSON-LD — коротко: название, дата, место, ссылка. Длинные описания
    съедали весь лимит текста, и на странице-афише модель видела одно-два события."""
    try:
        data = json.loads(block)
    except ValueError:
        return [block[:LD_DESC]] if "Event" in block else []
    out: list[str] = []

    def walk(x) -> None:
        if isinstance(x, list):
            for i in x:
                walk(i)
        elif isinstance(x, dict):
            types = x.get("@type") if isinstance(x.get("@type"), list) else [x.get("@type")]
            if any(isinstance(t, str) and t.endswith("Event") for t in types):
                loc = x.get("location")
                loc = loc[0] if isinstance(loc, list) and loc else loc
                offers = x.get("offers")
                offers = offers[0] if isinstance(offers, list) and offers else offers
                ev = {"name": x.get("name"), "startDate": x.get("startDate"),
                      "place": (loc.get("name") if isinstance(loc, dict) else loc),
                      "price": (offers.get("price") if isinstance(offers, dict) else None),
                      "url": x.get("url"), "description": str(x.get("description") or "")[:LD_DESC]}
                out.append(json.dumps({k: v for k, v in ev.items() if v}, ensure_ascii=False))
            for v in x.values():
                if isinstance(v, (list, dict)):
                    walk(v)

    walk(data)
    return out


def page_text(raw: str) -> str:
    """События из JSON-LD — коротко, потом видимый текст со ссылками, без шапки, меню и подвала."""
    blocks = re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', raw, re.S | re.I)
    ld = "\n".join(line for b in blocks for line in _ld_events(b.strip()))[:PAGE_CHARS * 2 // 3]
    main = re.search(r"<main\b[^>]*>(.*)</main>", raw, re.S | re.I)
    junk_tags = "script|style|noscript|svg" if main else "script|style|noscript|svg|header|nav|footer|aside|form"
    body = re.sub(rf"<({junk_tags})\b.*?</\1>", " ", main.group(1) if main else raw, flags=re.S | re.I)
    body = re.sub(r'<a\b[^>]*href="(https?://[^"]+)"[^>]*>', r" [\1] ", body, flags=re.I)  # ссылки на сами события
    body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    body = re.sub(r"\s+", " ", body).strip()
    return (ld + "\n" + body)[:PAGE_CHARS]


def search(client: httpx.Client, s: Settings, query: str) -> list[str]:
    if not s.brave_api_key:
        return []
    try:
        r = client.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": 5},
                       headers={"X-Subscription-Token": s.brave_api_key, "Accept": "application/json"})
        r.raise_for_status()
        return [x["url"] for x in r.json().get("web", {}).get("results", []) if x.get("url")]
    except Exception as e:
        log.warning("events_search_failed %r: %r", query, e)
        return []


def fetch(client: httpx.Client, url: str) -> str | None:
    try:
        r = client.get(url, follow_redirects=True)
        r.raise_for_status()
        return page_text(r.text)
    except Exception as e:
        log.warning("events_fetch_failed %s: %r", url, e)
        return None


NOT_FOUND = ("stránka nenalezena", "page not found", "404 not found", "страница не найдена")
AI = re.compile(r"\b(AI|A\.I\.|ML|LLM|GPT|umělá inteligence|umělé inteligence|artificial intelligence|machine learning"
                r"|strojové učení|искусственн\w+ интеллект|нейросет\w*|data science)\b", re.I)


def junk(text: str) -> str | None:
    """Почему страницу из поиска не стоит показывать модели (None — стоит): за модель платим,
    а поиск приносит 404 и общие афиши города без слова об ИИ."""
    if any(p in text[:2000].lower() for p in NOT_FOUND):
        return "not_found"
    if not AI.search(text):
        return "no_ai"
    return None


LANG_SEGMENT = re.compile(r"^https?://(?:www\.)?([^/]+)/(?:cs|en|ru|de)(?=/|$)")


def same_page(url: str) -> str:
    """Одна страница на разных языках — одна: /cs/… и /en/… — одно событие с разными названиями."""
    url = url.split("#")[0].split("?")[0].rstrip("/").lower()
    return LANG_SEGMENT.sub(r"\1", url) if LANG_SEGMENT.match(url) else re.sub(r"^https?://(?:www\.)?", "", url)


def slug(text: str) -> str:
    return re.sub(r"[^\w]+", "-", text.lower()).strip("-")[:80]


DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{1,2}:\d{2}))?")


def normalize(raw: dict, start: datetime, end: datetime, source: str, zone: ZoneInfo) -> list[dict]:
    """Ответ модели → события в окне [start, end). Без даты, названия или вне окна — мимо.
    Дату ищем внутри строки: модель любит дописать «(local time)» или секунды."""
    out = []
    for e in raw.get("found") or []:
        if not isinstance(e, dict):
            continue
        title = str(e.get("title") or "").strip()[:300]
        m = DATE_RE.search(str(e.get("starts_at") or ""))
        if not title or not m:
            continue
        hh, mm = (m[2] or "0:00").split(":")
        try:
            when = datetime.fromisoformat(m[1]).replace(hour=int(hh), minute=int(mm), tzinfo=zone)
        except ValueError:
            continue
        # без времени — в окне, если подходит день: сегодняшнее событие «без часов» не выпадает
        inside = (start <= when < end if m[2]
                  else start.astimezone(zone).date() <= when.date() < end.astimezone(zone).date())
        if not inside:
            continue
        url = str(e.get("url") or "")
        out.append({
            "key": f"{when.date().isoformat()}:{slug(title)}"[:200], "title": title,
            "starts_at": when.astimezone(timezone.utc).replace(tzinfo=None), "has_time": bool(m[2]),
            "place": str(e.get("place") or "")[:300], "price": str(e.get("price") or "")[:100],
            "free": bool(e.get("free")), "lang": str(e.get("lang") or "")[:10],
            "summary": str(e.get("summary_ru") or "")[:400],
            "url": (url if url.startswith("http") else source)[:1000],
        })
    return out


def collect(db, s: Settings, llm, cfg: dict, now: datetime, client: httpx.Client | None = None) -> int:
    """Один сбор: страницы + поиск → модель → новые события в базе. now — UTC без зоны. Возвращает число новых."""
    zone, now_utc = tz(cfg), now.replace(tzinfo=timezone.utc)
    own = client is None
    client = client or httpx.Client(timeout=20, headers={"User-Agent": UA})
    try:
        fixed = list(cfg.get("pages") or [])
        found = [u for q in cfg.get("searches") or [] for u in search(client, s, q)]
        found = [u for u in dict.fromkeys(found) if u not in fixed]
        # страница события, которое уже в базе (в том числе на другом языке), ничего нового не даст — не читаем
        stored = {same_page(u) for u in db.scalars(select(Event.url).where(Event.starts_at >= now - timedelta(days=1))) if u}
        urls = fixed + [u for u in found if same_page(u) not in stored]
        texts = [fetch(client, u) for u in urls]
    finally:
        if own:
            client.close()
    pages, skipped = [], {}
    for u, t in zip(urls, texts):
        why = "unreachable" if not t else None if u in fixed else junk(t)  # свои страницы-афиши читаем всегда
        if why:
            skipped[why] = skipped.get(why, 0) + 1
        else:
            pages.append((u, t))
    # страница не изменилась с последнего чтения (и читали меньше недели назад) — события с неё уже в базе
    digest = {u: hashlib.sha256(t.encode()).hexdigest() for u, t in pages}
    seen = {p.url: p for p in db.scalars(select(EventPage).where(EventPage.url.in_(list(digest))))}
    unchanged = {u for u, p in seen.items() if p.text_hash == digest[u] and p.read_at > now - timedelta(days=REREAD_DAYS)}
    pages = [(u, t) for u, t in pages if u not in unchanged][:MAX_PAGES]
    if not pages:
        log.info("афиша: читать нечего (пропущено %s, без изменений %d)", skipped, len(unchanged))
        return 0

    local = now_utc.astimezone(zone)
    start, end = now_utc, now_utc + timedelta(days=WINDOW_DAYS)
    system = EXTRACT.format(city=cfg.get("city", "Praha"), today=local.strftime("%A %Y-%m-%d"), tz=zone.key,
                            start=local.date().isoformat(), end=(local + timedelta(days=WINDOW_DAYS)).date().isoformat())
    items: list[dict] = []
    for i in range(0, len(pages), BATCH):
        batch = pages[i:i + BATCH]
        try:
            raw = ask_json(llm, system, "\n\n".join(f"SOURCE {u}\n{t}" for u, t in batch))
        except Exception as e:
            log.warning("events_extract_failed %s: %r", [u for u, _ in batch], e)
            continue  # отпечаток не сохраняем — завтра страницу прочитают снова
        items += normalize(raw, start, end, batch[0][0] if len(batch) == 1 else "", zone)
        for u, _ in batch:
            page = seen.get(u) or EventPage(url=u)
            page.text_hash, page.read_at = digest[u], now
            db.add(page)

    existing = set(db.scalars(select(Event.key).where(Event.key.in_({x["key"] for x in items})))) if items else set()
    # та же страница события на другом языке — тот же день, другое название. Ссылку на прочитанную афишу
    # не считаем: с одной афиши в один день бывает несколько событий
    read = {same_page(u) for u, _ in pages}
    upcoming_pages = {(e.starts_at.date(), same_page(e.url))
                      for e in db.scalars(select(Event).where(Event.starts_at >= now - timedelta(days=1))) if e.url}
    new = 0
    for x in items:
        page = (x["starts_at"].date(), same_page(x["url"])) if x["url"] else None
        if x["key"] in existing or (page and page[1] not in read and page in upcoming_pages):
            continue
        existing.add(x["key"])
        if page and page[1] not in read:
            upcoming_pages.add(page)
        db.add(Event(**x, found_at=now))
        new += 1
    db.commit()
    log.info("афиша: страниц %d, пропущено %s, событий %d, новых %d", len(pages), skipped, len(items), new)
    return new


# ───────────────────────── в выпуск ─────────────────────────

def upcoming(db, now: datetime, days: int = ANNOUNCE_DAYS) -> list[Event]:
    """События на ближайшие дни, с начала сегодняшнего: «без часов» хранятся на полночь и не должны пропадать с утра."""
    return list(db.scalars(select(Event).where(Event.starts_at >= now - timedelta(hours=26),
                                               Event.starts_at < now + timedelta(days=days))
                           .order_by(Event.starts_at)))


def to_announce(db, now: datetime, cfg: dict) -> list[Event]:
    """Что рассказать сегодня: есть новые события — всю неделю вперёд, иначе блока нет.
    В понедельник — обзор недели, даже если нового ничего. Вчерашнее и прошедшее не показываем."""
    zone = tz(cfg)
    today = now.replace(tzinfo=timezone.utc).astimezone(zone).date()
    evs = [e for e in upcoming(db, now) if _aware(e.starts_at).astimezone(zone).date() >= today]
    if any(not e.announced for e in evs) or (today.weekday() == 0 and evs):
        return evs
    return []


def when(e: Event, cfg: dict) -> str:
    """«чт 23.10, 19:00»"""
    t = _aware(e.starts_at).astimezone(tz(cfg))
    return f"{WEEKDAYS[t.weekday()]} {t:%d.%m}" + (f", {t:%H:%M}" if e.has_time else "")


def line(e: Event, cfg: dict) -> str:
    """«чт 23.10, 19:00 — AI Tinkerers (бесплатно), Impact Hub»"""
    price = "бесплатно" if e.free else e.price
    out = f"{when(e, cfg)} — {e.title}"
    if price:
        out += f" ({price})"
    if e.place:
        out += f", {e.place.split(',')[0]}"
    return out
