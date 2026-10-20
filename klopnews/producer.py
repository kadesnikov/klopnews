"""Производство: сюжеты дня → клипы (один раз на всех) → персональные выпуски (склейка под каждого)."""
import logging
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select

from klopnews import editor
from klopnews.config import Settings, catalog
from klopnews.db import Edition, Episode, Story, User
from klopnews.llm import make_llm
from klopnews.render import studio
from klopnews.tts import RATE, synthesize

log = logging.getLogger(__name__)
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]
OUTRO = ("Это были Клоп Новости. Все ссылки на источники — под видео, проверяйте и делитесь. "
         "Берегите себя и друг друга. До завтра!")


def date_label(edition: str) -> str:
    d = date.fromisoformat(edition)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def _anchor_wav(text: str, s: Settings) -> Path:
    return synthesize(text, s.anchor_voice, s.anchor_instructions, s)


def render_story(st: Story, s: Settings, cat: dict) -> None:
    color = cat["topics"].get(st.topic, {}).get("color", "#3b82f6")
    image = studio.fetch_image(st.image_url, s.media_dir / "img")
    base = s.media_dir / "clips" / st.edition
    label = date_label(st.edition)
    scene = studio.Scene(kind="studio", date_label=label, tag=st.tag or cat["topics"][st.topic]["title"].upper(),
                         color=color, title=st.headline, bullets=st.bullets, image=image,
                         quotes=st.data.get("quotes", []))
    if st.kind == "brief":
        scene.lower = f"Коротко: {st.headline}"
    out = base / f"{st.id}.mp4"
    if not st.reporter_script:
        st.duration = studio.render_segment(scene, _anchor_wav(st.script, s), out)
    else:
        a, b = base / f"{st.id}_a.mp4", base / f"{st.id}_b.mp4"
        studio.render_segment(scene, _anchor_wav(st.script, s), a, tail=0.2)
        report = studio.Scene(kind="report", date_label=label, tag=st.place.upper() if st.place else "НА МЕСТЕ",
                              color="#ff5fa2", title=st.place or st.headline, image=image,
                              image_credit=st.sources[0]["name"] if image and st.sources else "",
                              lower=st.headline, nameplate=("Клопик", f"корреспондент · {st.place or 'на месте событий'}"))
        studio.render_segment(report, synthesize(st.reporter_script, s.reporter_voice, s.reporter_instructions, s), b)
        st.duration = studio.concat([a, b], out)
        a.unlink()
        b.unlink()
    st.clip_path = str(out.relative_to(s.media_dir))
    st.status = "ready"


def build_edition(db, s: Settings, edition: str, now: datetime) -> None:
    """Тексты, озвучка и клипы всех сюжетов дня.

    Повторный вызов (после перезапуска воркера) не переписывает готовое: дописывает новые события и рендерит недостающее.
    """
    ed = db.get(Edition, edition) or Edition(date=edition)
    ed.status = "building"
    db.add(ed)
    db.commit()
    cat = catalog()
    have = db.scalars(select(Story).where(Story.edition == edition)).all()
    done = frozenset(src["url"] for st in have for src in st.sources)
    for st in editor.build_stories(db, make_llm(s), cat, edition, now, s.reporter_enabled, s.reporters_per_edition,
                                   done_urls=done, markets_done=any(st.kind == "markets" for st in have)):
        db.add(st)
        db.commit()  # сразу: перезапуск воркера не потеряет написанное
    todo = db.scalars(select(Story).where(Story.edition == edition, Story.status != "ready")
                      .order_by(Story.importance.desc())).all()
    for st in todo:
        try:
            render_story(st, s, cat)
        except Exception as e:
            log.exception("render_failed story=%s: %r", st.id, e)
            st.status = "failed"
        db.commit()
    ed.status = "ready"
    db.commit()


def pick_stories(user: User, stories: list[Story], budget: float) -> list[Story]:
    """Сюжеты под настройки человека, по важности, пока влезают в его длительность.

    Порядок в выпуске: главные сюжеты, рынки, потом «коротко».
    """
    muted = set(user.muted_sources or [])
    ok = [st for st in stories if st.status == "ready"
          and (not user.topics or st.topic in user.topics)
          and (st.kind == "markets" or any(src["id"] not in muted for src in st.sources))]
    chosen, used = [], 0.0
    for st in sorted(ok, key=lambda x: -x.importance):
        if used + st.duration <= budget:
            chosen.append(st)
            used += st.duration
    rank = {"story": 0, "markets": 1, "brief": 2}
    return sorted(chosen, key=lambda x: (rank[x.kind], -x.importance))


def intro_script(user: User, picked: list[Story]) -> str:
    hour = int(user.delivery_time.split(":")[0])
    greet = "Доброе утро" if hour < 12 else ("Добрый день" if hour < 18 else "Добрый вечер")
    heads = [st.headline.rstrip(".") for st in picked if st.kind == "story"][:3]
    menu = (" Сегодня в выпуске: " + "; ".join(heads[:-1]) + (" и " if len(heads) > 1 else "") + heads[-1] + ".") if heads else ""
    return f"{greet}, {user.name}! В эфире Клоп Новости, с вами Клоп Александрович.{menu} Поехали!"


def assemble(db, s: Settings, user: User, edition: str) -> Episode | None:
    cat = catalog()
    stories = db.scalars(select(Story).where(Story.edition == edition)).all()
    picked = pick_stories(user, stories, user.minutes * 60 - 35)
    if not picked:
        log.warning("nothing to show user=%s edition=%s", user.id, edition)
        return None
    label = date_label(edition)
    ep = Episode(user_id=user.id, edition=edition)
    db.add(ep)
    db.flush()
    work = s.media_dir / "clips" / edition
    topics = [t for t in cat["topics"] if any(st.topic == t for st in picked)]
    intro = studio.Scene(kind="studio", date_label=label, title=f"Привет, {user.name}!",
                         subtitle=f"Выпуск на {label}", color="#e23636",
                         pills=[(cat["topics"][t]["title"], cat["topics"][t]["color"]) for t in topics],
                         lower="Ваш персональный выпуск новостей", tag="КЛОП·НОВОСТИ",
                         nameplate=("Клоп Александрович", "ведущий · AI-ассистент · клоп в машине"))
    intro_path = work / f"intro_{ep.slug}.mp4"
    intro_dur = studio.render_segment(intro, _anchor_wav(intro_script(user, picked), s), intro_path,
                                      prefix=studio.jingle(RATE))
    outro_path = work / "outro.mp4"
    if not outro_path.exists():
        outro = studio.Scene(kind="studio", date_label=label, title="До завтра!", subtitle="Клоп·Новости — семейный выпуск",
                             color="#e23636", lower="Ссылки на все источники — под видео", tag="КЛОП·НОВОСТИ")
        studio.render_segment(outro, _anchor_wav(OUTRO, s), outro_path, suffix=studio.jingle(RATE, up=False))

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
