"""Производство: сюжеты дня → клипы (один раз на всех) → персональные выпуски (склейка под каждого)."""
import fcntl
import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select

from klopnews import editor, events, holidays, weather
from klopnews.config import Settings, catalog, optional_topics
from klopnews.db import Edition, Episode, Story, User
from klopnews.llm import make_llm
from klopnews.render import studio
from klopnews.tts import RATE, synthesize

log = logging.getLogger(__name__)
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]
OUTRO = ("Это были Клоп Новости. Все ссылки на источники — под видео, проверяйте и делитесь. "
         "Берегите себя и друг друга. До завтра!")
FRAME = 35  # интро и финал выпуска, с


def date_label(edition: str) -> str:
    d = date.fromisoformat(edition)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def _anchor_wav(text: str, s: Settings) -> Path:
    return synthesize(text, s.anchor_voice, s.anchor_instructions, s)


def credit_text(st: Story) -> str:
    if st.kind == "events":  # в sources афиши — сами мероприятия, а не издания
        return ""
    names = list(dict.fromkeys(src["name"] for src in st.sources))
    return (("Источник: " if len(names) == 1 else "Источники: ") + ", ".join(names)) if names else ""


def render_story(st: Story, s: Settings, cat: dict, ticker: str = "") -> None:
    color = cat["topics"].get(st.topic, {}).get("color", "#3b82f6")
    image = studio.fetch_image(st.image_url, s.media_dir / "img")
    base = s.media_dir / "clips" / st.edition
    label = date_label(st.edition)
    theme = holidays.theme_for(date.fromisoformat(st.edition))
    scene = studio.Scene(kind="studio", date_label=label, tag=st.tag or cat["topics"][st.topic]["title"].upper(),
                         color=color, title=st.headline, bullets=st.bullets, image=image,
                         quotes=st.data.get("quotes", []), movers=st.data.get("movers", []), ticker=ticker,
                         credit=credit_text(st), theme=theme)
    if st.kind == "brief":
        scene.lower = f"Коротко: {st.headline}"
    elif st.kind == "events":
        scene.lower = "Ссылки на все события — под видео"
    out = base / f"{st.id}.mp4"
    if st.kind == "weather":
        w, city = st.data["weather"], st.data.get("city", "")
        scene = studio.Scene(kind="weather", date_label=label, title=f"{city}, сегодня", weather=w,
                             gear=weather.gear(w), tag="ПОГОДА", color=color, lower=f"{city}: {weather.summary(w)}",
                             nameplate=("Клопик", f"прогноз погоды · {city}"), ticker=ticker, theme=theme)
        st.duration = studio.render_segment(scene, synthesize(st.reporter_script, s.reporter_voice,
                                                              s.reporter_instructions, s), out)
    elif not st.reporter_script:
        st.duration = studio.render_segment(scene, _anchor_wav(st.script, s), out)
    else:
        a, b = base / f"{st.id}_a.mp4", base / f"{st.id}_b.mp4"
        studio.render_segment(scene, _anchor_wav(st.script, s), a, tail=0.2)
        report = studio.Scene(kind="report", date_label=label, tag=st.place.upper() if st.place else "НА МЕСТЕ",
                              color="#ff5fa2", title=st.place or st.headline, image=image,
                              image_credit=st.sources[0]["name"] if image and st.sources else "",
                              lower=st.headline, nameplate=("Клопик", f"корреспондент · {st.place or 'на месте событий'}"),
                              ticker=ticker, theme=theme)
        studio.render_segment(report, synthesize(st.reporter_script, s.reporter_voice, s.reporter_instructions, s), b)
        st.duration = studio.concat([a, b], out)
        a.unlink()
        b.unlink()
    st.clip_path = str(out.relative_to(s.media_dir))
    st.status = "ready"


@contextmanager
def build_lock(s: Settings):
    """Одно производство на сервер: воркер и ручные команды из cli не рендерят одно и то же параллельно."""
    s.data_dir.mkdir(parents=True, exist_ok=True)
    with open(s.data_dir / "build.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def build_edition(db, s: Settings, edition: str, now: datetime, write: bool = True) -> None:
    """Тексты всех сюжетов дня; озвучка и клипы — тех, что войдут хоть в чей-то выпуск. Вызывать под build_lock.

    Повторный вызов (после перезапуска воркера) не переписывает готовое: дописывает новые события и рендерит недостающее.
    write=False — только озвучить и отрендерить то, что не готово (rerender).
    """
    ed = db.get(Edition, edition) or Edition(date=edition)
    ed.status = "building"
    db.add(ed)
    db.commit()
    cat = catalog()
    if write:
        have = db.scalars(select(Story).where(Story.edition == edition)).all()
        done = frozenset(src["url"] for st in have for src in st.sources)
        for st in editor.build_stories(db, make_llm(s), cat, edition, now, s.reporter_enabled, s.reporters_per_edition,
                                       done_urls=done, markets_done=any(st.kind == "markets" for st in have)):
            db.add(st)
            db.commit()  # сразу: перезапуск воркера не потеряет написанное
            log.info("сюжет написан: [%s] %s", st.topic, st.headline)
        if "ai" in cat["topics"] and cat["events"] and not any(st.kind == "events" for st in have):
            afisha(db, s, cat, edition, now)
        if "weather" in cat["topics"] and cat.get("weather") and not any(st.kind == "weather" for st in have):
            forecast(db, s, cat, edition)
    stories = db.scalars(select(Story).where(Story.edition == edition)).all()
    todo = to_voice(db.scalars(select(User)).all(), stories, s.voice_margin, optional_topics(cat),
                    date.fromisoformat(edition))
    log.info("озвучиваем %d сюжетов из %d: остальные не влезают ни в чей выпуск", len(todo), len(stories))
    render_stories(db, s, todo, stories)
    ed.status = "ready"
    db.commit()
    log.info("выпуск дня %s готов", edition)


def render_stories(db, s: Settings, todo: list[Story], day: list[Story]) -> None:
    """Озвучить и отрендерить todo, самые важные первыми. day — все сюжеты дня, для бегущей строки."""
    cat, tick = catalog(), ticker_text(day)
    todo = sorted(todo, key=lambda x: -x.importance)
    prefetch_audio(todo, s)
    for i, st in enumerate(todo, 1):
        try:
            render_story(st, s, cat, tick)
            log.info("клип %d/%d готов: %s", i, len(todo), st.headline)
        except Exception as e:
            log.exception("render_failed story=%s: %r", st.id, e)
            st.status = "failed"
        db.commit()


def forecast(db, s: Settings, cat: dict, edition: str) -> None:
    """Погода от Клопика. Прогноз не скачался — выпуск выходит без неё."""
    cfg = cat["weather"]
    w = weather.fetch(cfg)
    if not w:
        return
    db.add(editor.write_weather(make_llm(s), w, cfg.get("city", "Прага"), edition))
    db.commit()
    log.info("погода: %s", weather.summary(w))


def afisha(db, s: Settings, cat: dict, edition: str, now: datetime) -> None:
    """Блок «ИИ в Праге»: сбор ивентов и сюжет по ним. Не получилось — выпуск выходит без него."""
    llm = make_llm(s)
    try:
        events.collect(db, s, llm, cat["events"], now)
        evs = events.to_announce(db, now, cat["events"])
        st = editor.write_events(llm, evs, cat["events"], edition)
    except Exception as e:
        db.rollback()
        log.error("afisha_failed: %r", e)
        return
    if st:
        for e in evs:
            e.announced = True
        db.add(st)
        db.commit()
        log.info("афиша: %d событий в выпуске", len(evs))


def prefetch_audio(stories: list[Story], s: Settings, workers: int = 4) -> None:
    """Озвучка — сетевой запрос на 5–15 с, а рендер ждёт её по очереди. Заказываем всю разом в несколько потоков."""
    jobs = [(st.script, s.anchor_voice, s.anchor_instructions) for st in stories if st.script]
    jobs += [(st.reporter_script, s.reporter_voice, s.reporter_instructions) for st in stories if st.reporter_script]

    def one(job):
        try:
            synthesize(*job, s)
        except Exception as e:  # не страшно: рендер попробует ещё раз и честно упадёт
            log.warning("tts_prefetch_failed: %r", e)

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, jobs))
    log.info("озвучено сюжетов: %d", len(jobs))


def ticker_text(stories: list[Story], n: int = 10) -> str:
    """Бегущая строка: главные заголовки дня (общая для всех клипов дня — клипы рендерятся один раз на всех)."""
    top = sorted((st for st in stories if st.kind == "story"), key=lambda x: -x.importance)[:n]
    return "     •     ".join(st.headline for st in top)


def _key_words(headline: str) -> set[str]:
    """Имена, названия и числа из заголовка, обрезанные до 5 букв: «Вьетнаме» и «Вьетнам» совпадут."""
    words = re.findall(r"[\w,.]+", headline)
    return {w.strip(",.")[:5].lower() for w in words
            if w[:1].isupper() or any(c.isdigit() for c in w) or w.isascii() and w[:1].isalpha()}


def estimate(st: Story) -> float:
    """Длительность клипа до озвучки: с ускорением tts_speed выходит около 2.7 слова в секунду."""
    return len(f"{st.script} {st.reporter_script or ''}".split()) / 2.7 + 1


def pick_stories(user: User, stories: list[Story], budget: float, optional: frozenset = frozenset(),
                 planning: bool = False) -> list[Story]:
    """Сюжеты под настройки человека, по важности, примерно на его длительность.

    Длительность — ориентир, а не потолок: сюжет, который начинается до конца бюджета, берём, если выпуск выйдет
    длиннее не больше чем на 20%. Мало новостей — выпуск честно короче.
    Короткий блок «Рынки» (индексы, акции дня, курсы) — всем, кто не отключил тему «Рынки и деньги»,
    и вне очереди: иначе важные сюжеты выедают время раньше него.
    Погода от Клопика — тоже вне очереди и сразу после интро.
    optional — темы по желанию: при «все темы» (пустой список) они не попадают.
    Порядок в выпуске: погода, главные сюжеты, рынки, «ИИ в Праге», потом «коротко».
    planning — ещё до озвучки: неозвученные тоже годятся, их длительность оценивается по тексту.
    """
    muted = set(user.muted_sources or [])
    ok = [st for st in stories if (st.status != "failed" if planning else st.status == "ready")
          and (st.topic in user.topics if user.topics else st.topic not in optional)
          and (st.kind in ("markets", "events", "weather") or any(src["id"] not in muted for src in st.sources))]
    chosen, used, seen, urls = [], 0.0, [], set()
    for st in sorted(ok, key=lambda x: (x.kind not in ("weather", "markets"), -x.importance)):
        keys, own = _key_words(st.headline), {src["url"] for src in st.sources}
        if any(len(keys & k) >= 2 for k in seen) or own & urls:  # то же событие другим сюжетом или в другой теме
            continue
        dur = st.duration if st.status == "ready" else estimate(st)
        if used < budget and used + dur <= budget * 1.2:
            chosen.append(st)
            seen.append(keys)
            urls |= own
            used += dur
    rank = {"weather": -1, "story": 0, "markets": 1, "events": 2, "brief": 3}
    return sorted(chosen, key=lambda x: (rank[x.kind], -x.importance))


def to_voice(users: list[User], stories: list[Story], margin: float, optional: frozenset = frozenset(),
             day: date | None = None) -> list[Story]:
    """Неозвученные сюжеты, которые попадут хоть к кому-то в выпуск.

    Озвучка — главный расход, а 40 сюжетов дня не нужны никому целиком. margin — запас по длительности:
    оценка по тексту неточная, а лишнее при склейке всё равно отрежется. day — день выпуска: от него зависит длина.
    """
    need = {id(st) for u in users
            for st in pick_stories(u, stories, ((u.minutes_on(day) if day else u.minutes) * 60 - FRAME) * margin,
                                   optional, planning=True)}
    return [st for st in stories if id(st) in need and st.status == "new"]


def intro_script(user: User, picked: list[Story], day: date | None = None) -> str:
    day = day or date.today()
    hour = int(user.delivery_time.split(":")[0])
    greet = "Доброе утро" if hour < 12 else ("Добрый день" if hour < 18 else "Добрый вечер")
    heads = [st.headline.rstrip(".") for st in picked if st.kind == "story"][:3]
    menu = (" Сегодня в выпуске: " + "; ".join(heads[:-1]) + (" и " if len(heads) > 1 else "") + heads[-1] + ".") if heads else ""
    extra = ""
    if holidays.is_birthday(user.birthday, day):
        extra += (f" {user.name}, с днём рождения! Вся редакция Клоп Новостей поздравляет: здоровья, радости "
                  "и побольше хороших новостей.")
    theme = holidays.theme_for(day)
    if theme and theme.greeting:
        extra += " " + theme.greeting
    weather_first = " А сначала — погода от Клопика." if any(st.kind == "weather" for st in picked) else ""
    return f"{greet}, {user.name}! В эфире Клоп Новости, с вами Клоп Александрович.{extra}{menu}{weather_first} Поехали!"


def assemble(db, s: Settings, user: User, edition: str) -> Episode | None:
    cat = catalog()
    stories = db.scalars(select(Story).where(Story.edition == edition)).all()
    optional = optional_topics(cat)
    day = date.fromisoformat(edition)
    if missing := to_voice([user], stories, s.voice_margin, optional, day):  # новенький или поменял настройки после сборки
        render_stories(db, s, missing, stories)
    picked = pick_stories(user, stories, user.minutes_on(day) * 60 - FRAME, optional)
    if not picked:
        log.warning("nothing to show user=%s edition=%s", user.id, edition)
        return None
    label = date_label(edition)
    ep = Episode(user_id=user.id, edition=edition)
    db.add(ep)
    db.flush()
    work = s.media_dir / "clips" / edition
    topics = [t for t in cat["topics"] if any(st.topic == t for st in picked)]
    theme, birthday = holidays.theme_for(day), holidays.is_birthday(user.birthday, day)
    intro = studio.Scene(kind="studio", date_label=label,
                         title=f"С днём рождения, {user.name}!" if birthday else f"Привет, {user.name}!",
                         subtitle=f"Выпуск на {label}", color="#e23636",
                         pills=[(cat["topics"][t]["title"], cat["topics"][t]["color"]) for t in topics],
                         lower="Праздничный выпуск — только для тебя" if birthday else "Ваш персональный выпуск новостей",
                         tag="КЛОП·НОВОСТИ", nameplate=("Клоп Александрович", "ведущий · AI-ассистент · клоп в машине"),
                         ticker=ticker_text(picked), theme=holidays.BIRTHDAY if birthday else theme)
    intro_path = work / f"intro_{ep.slug}.mp4"
    intro_dur = studio.render_segment(intro, _anchor_wav(intro_script(user, picked, day), s), intro_path,
                                      prefix=studio.jingle(RATE))
    outro_wav = _anchor_wav(OUTRO, s)
    outro = studio.Scene(kind="studio", date_label=label, title="До завтра!", subtitle="Клоп·Новости — семейный выпуск",
                         color="#e23636", lower="Ссылки на все источники — под видео  •  made by kadesnikov.com",
                         tag="КЛОП·НОВОСТИ", ticker=ticker_text(stories), theme=theme)
    # имя от озвучки, надписей и праздника: сменили голос или текст на экране — финал перерендерится
    key = hashlib.sha256(f"{outro_wav.stem}|{outro.title}|{outro.subtitle}|{outro.lower}|{theme and theme.id}"
                         .encode()).hexdigest()[:12]
    outro_path = work / f"outro_{key}.mp4"
    if not outro_path.exists():
        studio.render_segment(outro, outro_wav, outro_path, suffix=studio.jingle(RATE, up=False))

    chapters, t = [{"t": 0, "title": "Начало выпуска", "topic": "", "sources": []}], intro_dur
    for st in picked:
        chapters.append({"t": round(t, 1), "title": st.headline, "topic": st.topic, "kind": st.kind,
                         "reporter": bool(st.reporter_script), "sources": st.sources})
        t += st.duration
    out = s.media_dir / "episodes" / f"{ep.slug}.mp4"
    ep.duration = studio.concat([intro_path, *[s.media_dir / st.clip_path for st in picked], outro_path], out)
    studio.ffmpeg(["-ss", "2.5", "-i", str(intro_path), "-frames:v", "1", "-q:v", "3",
                   str(out.with_suffix(".jpg"))])
    intro_path.unlink()
    ep.chapters = chapters
    db.commit()
    return ep
