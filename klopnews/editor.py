"""Редакция: из сырых новостей за сутки — сюжеты дня с текстами в характере Клопа.

1. По каждой теме модель отбирает события и склеивает дубли из разных источников.
2. По каждому событию качаем полные тексты статей и пишем сюжет: главный (~1 мин) или «коротко» (~20 с).
3. Пара лучших событий с конкретным местом получает прямое включение Клопика.
4. Блок «Рынки» — по настоящим котировкам.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from klopnews import feeds, markets
from klopnews.db import Item, Story
from klopnews.llm import ask_json

log = logging.getLogger(__name__)

PERSONA = """Ты — Клоп Александрович, ведущий семейного видеовыпуска «Клоп·Новости».
По натуре ты Клоп — AI-ассистент, «клоп в машине»: прямой, дружелюбный, без воды и корпоративной болтовни.
У тебя есть мнения: можешь одобрить, удивиться, поворчать, найти что-то смешным или скучным.
Ты гость в доме у семьи и уважаешь приватность. Зрители — русскоязычная семья, живущая в Праге.
Твой корреспондент — Клопик, бойкая божья коровка с розовым бантиком."""

RULES = """Правила:
- Только факты из присланных материалов. Не выдумывай цифры, даты, цитаты и имена. Нет данных — не говори.
- Пиши для уха: короткие фразы, без скобок, списков, эмодзи, markdown и ссылок.
- Названия компаний и продуктов пиши латиницей (OpenAI, GPT, Apple), имена людей — по-русски.
- Материалы бывают на английском и чешском — пиши по-русски.
- Войны, катастрофы, гибель людей — серьёзно и сдержанно, без шуток и без мнения Клопа.
- Сюжеты потом переставляются, поэтому не пиши «в начале выпуска», «а теперь», «дорогие зрители».
- Ответ — только JSON, без пояснений."""

SELECT = PERSONA + """

Ты выпускающий редактор. Из новостей за сутки по теме «{topic}» выбери до {n} самых важных и интересных событий.
Одна история из разных источников — одно событие: перечисли все её номера.
Пропускай рекламу, мелочи, гороскопы, спорт без большого повода и то, что уже было в эфире, если нет нового развития.
importance — 1..10, насколько событие важно и интересно. reporter_ok — true, если у события есть конкретное место
(город, площадка, космодром), куда можно «отправить корреспондента», и это не трагедия.
Ответ — только JSON: {{"events": [{{"items": [3, 7], "importance": 8, "reporter_ok": false}}]}}"""

WRITE = PERSONA + "\n\n" + RULES + """

Напиши сюжет по материалам ниже. {length}
Формат: {{"headline": "заголовок до 70 знаков", "tag": "1–2 слова КАПСОМ: компания, страна или тема",
"bullets": ["2–3 коротких пункта для экрана, до 45 знаков каждый, без точек в конце"], "script": "текст для ведущего"{extra}}}"""

MAIN = ("Главный сюжет на минуту: script 110–150 слов. Что случилось, важные подробности, почему это важно "
        "или что это значит для нас. Если уместно, закончи одной фразой — мнением Клопа.")
BRIEF = "Короткая новость: script 35–55 слов, одна-две мысли, без мнения."
REPORTER = (', "anchor_lead": "1–2 фразы ведущего: подводка, в конце передай слово Клопику", '
            '"reporter_script": "90–130 слов от лица Клопика: включение с места событий, живо и по делу. Только факты '
            'из материалов, не выдумывай, что она видит вокруг. В конце верни слово в студию Клопу Александровичу.", '
            '"place": "место включения, коротко: Купертино, Прага, Байконур"')
REPORTER_NOTE = " Этот сюжет идёт с прямым включением: script не нужен, вместо него anchor_lead и reporter_script."

MARKETS = PERSONA + "\n\n" + RULES + """

Сделай короткий блок «Рынки» на 30–40 секунд (65–90 слов) по котировкам и новостям ниже — быстро, только главное:
1. Индексы: как сходили S&P 500, Nasdaq и DAX и что их двигало по новостям (ставка ФРС, отчёты компаний,
   инфляция, пошлины…) — одна-две фразы.
2. Самые заметные движения акций: называй сектор целиком, если акции движутся вместе («неоклауды Nebius и IREN
   падают…», «гиперскейлеры растут…»), и причину, если она есть в новостях. Нет причины — не придумывай.
3. Курс евро и доллара в кронах одной фразой.
Цифры — только из таблиц, проценты округляй до десятых. Никаких инвестиционных советов.
Формат: {"markets": true, "script": "текст для ведущего", "bullets": ["2–3 пункта для экрана"]}"""


def recent_items(db, sources: dict, topic: str, since: datetime, limit: int = 60) -> list[Item]:
    ids = [sid for sid, s in sources.items() if topic in s["topics"]]
    q = (select(Item).where(Item.source_id.in_(ids), Item.published_at >= since)
         .order_by(Item.published_at.desc()).limit(limit))
    return list(db.scalars(q))


def blocked_by_filter(e: Exception) -> bool:
    """Контент-фильтр Moonshot отклоняет запрос целиком, если ему не понравилась одна новость."""
    return "content_filter" in str(e) or "high risk" in str(e)


def select_events(llm, topic_title: str, items: list[Item], sources: dict, covered: list[str], n: int,
                  taken: list[str] = ()) -> list[dict]:
    lines = [f"[{i}] ({sources[it.source_id]['name']}) {it.title} — {it.summary[:200]}" for i, it in enumerate(items)]
    user = ("Уже были в эфире (не повторять, если нет нового развития): " + ("; ".join(covered) or "—")
            + "\n\nУже взяты в сегодняшний выпуск в других темах (эти события не выбирай совсем, даже из других "
            "источников): " + ("; ".join(taken) or "—")
            + "\n\nНовости:\n" + "\n".join(lines))
    try:
        events = ask_json(llm, SELECT.format(topic=topic_title, n=n), user).get("events", [])
    except Exception as e:
        if not blocked_by_filter(e):
            raise
        if len(items) == 1:
            log.warning("filtered out by model: %s", items[0].title)
            return []
        # делим пополам, пока не отсеется новость, которая не нравится фильтру
        half = len(items) // 2
        both = (select_events(llm, topic_title, items[:half], sources, covered, n, taken)
                + select_events(llm, topic_title, items[half:], sources, covered, n, taken))
        return sorted(both, key=lambda e: -e["importance"])[:n]
    out = []
    for ev in events:
        picked = [items[i] for i in ev.get("items", []) if isinstance(i, int) and 0 <= i < len(items)]
        if picked:
            out.append({"items": picked, "importance": int(ev.get("importance", 5)),
                        "reporter_ok": bool(ev.get("reporter_ok"))})
    return sorted(out, key=lambda e: -e["importance"])[:n]


def write_story(llm, ev: dict, topic: str, sources: dict, edition: str, reporter: bool) -> Story:
    main = ev["importance"] >= 6 or reporter
    parts, image = [], None
    for it in ev["items"][:3]:
        art = feeds.article(it.url) if len(parts) < 2 else {"text": "", "image": None}
        image = image or art["image"] or it.image_url  # og:image крупнее картинки из RSS
        parts.append(f"Источник: {sources[it.source_id]['name']}\nЗаголовок: {it.title}\n"
                     f"Анонс: {it.summary}\nТекст: {art['text'] or '—'}")
    system = WRITE.format(length=(MAIN if main else BRIEF) + (REPORTER_NOTE if reporter else ""),
                          extra=REPORTER if reporter else "")
    d = ask_json(llm, system, "\n\n---\n\n".join(parts))
    has_report = reporter and d.get("reporter_script") and d.get("anchor_lead")
    return Story(
        edition=edition, topic=topic, kind="story" if main else "brief", importance=ev["importance"],
        headline=str(d.get("headline") or ev["items"][0].title)[:300], tag=str(d.get("tag", ""))[:40].upper(),
        bullets=[str(b)[:80] for b in d.get("bullets", [])][:4],
        script=str(d["anchor_lead"] if has_report else d.get("script", "")),
        reporter_script=str(d["reporter_script"]) if has_report else None,
        place=(str(d.get("place") or "")[:80] or None) if has_report else None,
        sources=[{"id": it.source_id, "name": sources[it.source_id]["name"], "url": it.url} for it in ev["items"]],
        image_url=image,
    )


def write_markets(llm, quotes: list[dict], stocks: list[dict], items: list[Item], edition: str) -> Story | None:
    if not quotes:
        return None
    table = "\n".join(f"{q['name']}: {markets.fmt_price(q)} ({markets.fmt_change(q)} за день)" for q in quotes)
    shares = "\n".join(f"{q['name']} ({q['symbol']}, {q['group']}): {markets.fmt_change(q)}"
                       for q in sorted(stocks, key=lambda q: q["change_pct"])) or "—"
    news = "\n".join(f"- {it.title} — {it.summary[:200]}" for it in items[:25]) or "—"
    d = ask_json(llm, MARKETS, f"Котировки:\n{table}\n\nАкции (за день):\n{shares}\n\nНовости рынков и технологий:\n{news}")
    return Story(edition=edition, topic="finance", kind="markets", importance=7, headline="Рынки за день",
                 tag="РЫНКИ", bullets=[str(b)[:80] for b in d.get("bullets", [])][:3], script=str(d["script"]),
                 sources=[], data={"quotes": quotes, "movers": markets.top_movers(stocks)})


def build_stories(db, llm, cat: dict, edition: str, now: datetime, reporter_enabled: bool = True,
                  reporters: int = 2, per_topic: int = 8, done_urls: frozenset = frozenset(), markets_done: bool = False):
    """Тексты сюжетов дня (без озвучки), по одному — чтобы сохранять сразу.

    Ошибка в одном сюжете не валит выпуск. done_urls — события, уже написанные до перезапуска.
    """
    sources, since = cat["sources"], now - timedelta(hours=30)
    yday = (datetime.fromisoformat(edition) - timedelta(days=1)).date().isoformat()
    # новости, по которым уже были сюжеты в прошлые дни, модели второй раз не показываем
    used = {src["url"] for (srcs,) in db.execute(select(Story.sources).where(Story.edition < edition, Story.edition >= yday))
            for src in srcs}
    picked: list[tuple[str, dict]] = []
    # одна статья бывает в двух темах (Ars — и ИИ, и наука), одно событие — у разных источников разных тем:
    # что уже взято в выпуск, следующим темам не показываем и называем модели, чтобы не взяла его из другого источника
    taken = list(db.scalars(select(Story.headline).where(Story.edition == edition)))
    for topic, meta in cat["topics"].items():
        items = [it for it in recent_items(db, sources, topic, since) if it.url not in used]
        if not items:
            log.warning("no items for topic %s", topic)
            continue
        # вчерашние и уже написанные сегодня (если сборка дописывает день): иначе одно событие придёт дважды
        covered = list(db.scalars(select(Story.headline).where(Story.edition.in_([yday, edition]), Story.topic == topic)))
        try:
            events = select_events(llm, meta["title"], items, sources, covered, per_topic, taken)
            picked += [(topic, ev) for ev in events]
            used |= {it.url for ev in events for it in ev["items"]}
            taken += [ev["items"][0].title for ev in events]
            log.info("тема %s: %d новостей → %d событий", topic, len(items), len(events))
        except Exception as e:
            log.error("select_failed %s: %r", topic, e)

    # Прямые включения — самым важным событиям, у которых есть место
    report_ids = set()
    if reporter_enabled:
        cands = sorted((p for p in picked if p[1]["reporter_ok"]), key=lambda p: -p[1]["importance"])
        report_ids = {id(p[1]) for p in cands[:reporters]}

    for topic, ev in picked:
        if any(it.url in done_urls for it in ev["items"]):
            continue
        try:
            yield write_story(llm, ev, topic, sources, edition, id(ev) in report_ids)
        except Exception as e:
            log.error("write_failed %s: %r", ev["items"][0].title, e)

    if "finance" in cat["topics"] and cat["markets"] and not markets_done:
        try:
            news = recent_items(db, sources, "finance", since) + recent_items(db, sources, "ai", since, 10)
            st = write_markets(llm, markets.fetch_quotes(cat["markets"]), markets.fetch_quotes(cat.get("movers", [])),
                               news, edition)
            if st:
                yield st
        except Exception as e:
            log.error("markets_failed: %r", e)

