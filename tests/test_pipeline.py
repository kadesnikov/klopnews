"""Весь конвейер без сети: тестовые новости → сюжеты (fake LLM) → клипы (dummy TTS) → выпуск → страница."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from klopnews import producer
from klopnews.auth import sign
from klopnews.config import settings
from klopnews.db import Item, Story, User, session, utcnow
from klopnews.render import studio


def test_full_pipeline(monkeypatch):
    monkeypatch.setattr("klopnews.feeds.article", lambda url: {"text": "", "image": None})
    monkeypatch.setattr("klopnews.weather.fetch", lambda cfg: {"tmin": 7, "tmax": 12, "code": 63, "rain_prob": 85,
                                                               "rain_mm": 6.0, "wind": 6})
    monkeypatch.setattr("klopnews.markets.fetch_quotes", lambda ins: [
        {"name": "Nebius", "symbol": "NBIS", "group": "неоклауды", "price": 90.0, "change_pct": -7.5}]
        if ins and "group" in ins[0] else [
        {"name": "S&P 500", "price": 6000.0, "change_pct": 0.4}, {"name": "Золото", "unit": "$", "price": 2788.0, "change_pct": -1.2}])
    s = settings()
    with session() as db:
        for i, (src, title) in enumerate([("verge", "OpenAI выпустила модель"), ("irozhlas", "В Праге новое метро"),
                                          ("marketwatch", "Золото дешевеет")]):
            db.add(Item(source_id=src, url=f"https://ex.com/{i}", title=title, summary=title,
                        published_at=utcnow() - timedelta(hours=1)))
        user = User(tg_id=7, name="Маша", topics=[], muted_sources=[], minutes=5, delivery_time="07:30")
        db.add(user)
        db.commit()

        producer.build_edition(db, s, "2026-10-20", utcnow())
        stories = db.query(Story).all()
        assert {st.kind for st in stories} == {"story", "markets", "weather"}
        assert all(st.status == "ready" and st.duration > 1 for st in stories)
        assert sum(bool(st.reporter_script) for st in stories if st.kind == "story") == 2

        ep = producer.assemble(db, s, user, "2026-10-20")
        video = s.media_dir / "episodes" / f"{ep.slug}.mp4"
        assert video.exists() and abs(studio.probe_duration(video) - ep.duration) < 0.5
        assert len(ep.chapters) == 1 + len(stories)
        assert ep.chapters[1]["t"] > 3  # после интро с джинглом

    from klopnews.web import app

    client = TestClient(app)
    page = client.get(f"/e/{ep.slug}")
    assert page.status_code == 200 and "Маша" in page.text and "включение Клопика" in page.text
    assert client.get("/settings", follow_redirects=False).status_code == 303
    client.get(f"/login?t={sign(user.id, 60, s.session_secret)}&next=/settings")
    week = {f"minutes_{i}": "7" for i in range(5)} | {"minutes_5": "15", "minutes_6": "20"}
    r = client.post("/settings", data={"name": "Мария", "delivery_time": "08:15", **week,
                                       "topics": ["ai", "finance"], "sources": ["verge", "marketwatch"]})
    assert r.status_code == 200 and 'name="minutes_6"' in r.text
    with session() as db:
        u = db.get(User, user.id)
        assert (u.name, u.week_minutes, u.delivery_time, u.topics) == ("Мария", [7] * 5 + [15, 20], "08:15", ["ai", "finance"])
        assert u.minutes_on(date(2026, 11, 2)) == 7 and u.minutes_on(date(2026, 11, 7)) == 15  # пн и сб
        assert "verge" not in u.muted_sources and "irozhlas" in u.muted_sources


def test_article_in_two_topics_becomes_one_story(monkeypatch):
    monkeypatch.setattr("klopnews.feeds.article", lambda url: {"text": "", "image": None})
    monkeypatch.setattr("klopnews.markets.fetch_quotes", lambda ins: [])
    monkeypatch.setattr("klopnews.render.studio.render_segment", lambda *a, **k: 1.0)
    s = settings()
    with session() as db:
        db.add(Item(source_id="ars", url="https://ex.com/q", title="Quantinuum quantum record", summary="",
                    published_at=utcnow() - timedelta(hours=1)))  # Ars — в темах ai и science
        db.commit()
        producer.build_edition(db, s, "2026-10-20", utcnow())
        assert db.query(Story).count() == 1


def test_rebuild_resumes_without_duplicates(monkeypatch):
    monkeypatch.setattr("klopnews.feeds.article", lambda url: {"text": "", "image": None})
    monkeypatch.setattr("klopnews.markets.fetch_quotes", lambda ins: [])
    monkeypatch.setattr("klopnews.render.studio.render_segment", lambda *a, **k: 1.0)
    s = settings()
    with session() as db:
        for i in range(3):
            db.add(Item(source_id="verge", url=f"https://ex.com/{i}", title=f"Новость {i}", summary="",
                        published_at=utcnow() - timedelta(hours=1)))
        db.commit()
        producer.build_edition(db, s, "2026-10-20", utcnow())
        first = db.query(Story).count()
        producer.build_edition(db, s, "2026-10-20", utcnow())  # как после перезапуска воркера
        assert db.query(Story).count() == first == 3


def test_next_day_skips_items_already_used(monkeypatch):
    monkeypatch.setattr("klopnews.feeds.article", lambda url: {"text": "", "image": None})
    monkeypatch.setattr("klopnews.markets.fetch_quotes", lambda ins: [])
    monkeypatch.setattr("klopnews.render.studio.render_segment", lambda *a, **k: 1.0)
    s = settings()
    with session() as db:
        db.add(Item(source_id="verge", url="https://ex.com/old", title="Вчерашняя", summary="",
                    published_at=utcnow() - timedelta(hours=20)))
        db.commit()
        producer.build_edition(db, s, "2026-10-19", utcnow())
        db.add(Item(source_id="verge", url="https://ex.com/new", title="Сегодняшняя", summary="",
                    published_at=utcnow() - timedelta(hours=1)))
        db.commit()
        producer.build_edition(db, s, "2026-10-20", utcnow())
        today = db.query(Story).filter(Story.edition == "2026-10-20").all()
        assert [st.sources[0]["url"] for st in today] == ["https://ex.com/new"]


def test_rerender_only_renders(monkeypatch):
    monkeypatch.setattr("klopnews.feeds.article", lambda url: {"text": "", "image": None})
    monkeypatch.setattr("klopnews.markets.fetch_quotes", lambda ins: [])
    rendered = []
    monkeypatch.setattr("klopnews.render.studio.render_segment", lambda sc, *a, **k: rendered.append(sc.title) or 1.0)
    s = settings()
    with session() as db:
        db.add(Item(source_id="verge", url="https://ex.com/1", title="Первая", summary="",
                    published_at=utcnow() - timedelta(hours=1)))
        db.add(User(tg_id=7, name="Маша", topics=[], muted_sources=[], minutes=10))
        db.commit()
        with producer.build_lock(s):
            producer.build_edition(db, s, "2026-10-20", utcnow())
        db.add(Item(source_id="verge", url="https://ex.com/2", title="Свежая", summary="",
                    published_at=utcnow() - timedelta(minutes=5)))
        for st in db.query(Story).all():
            st.status = "new"
        db.commit()
        rendered.clear()
        with producer.build_lock(s):
            producer.build_edition(db, s, "2026-10-20", utcnow(), write=False)
        assert db.query(Story).count() == 1 and len(rendered) >= 1  # перерисовали, новых сюжетов не писали


def test_unwatched_topics_are_voiced_only_on_demand(monkeypatch):
    monkeypatch.setattr("klopnews.feeds.article", lambda url: {"text": "", "image": None})
    monkeypatch.setattr("klopnews.markets.fetch_quotes", lambda ins: [])
    s = settings()
    with session() as db:
        for i, src in enumerate(["verge", "irozhlas"]):
            db.add(Item(source_id=src, url=f"https://ex.com/{i}", title=f"Новость {i}", summary="",
                        published_at=utcnow() - timedelta(hours=1)))
        db.add(User(tg_id=1, name="Маша", topics=["ai"], muted_sources=[], minutes=10))
        db.commit()
        producer.build_edition(db, s, "2026-10-20", utcnow())
        assert {st.topic: st.status for st in db.query(Story).all()} == {"ai": "ready", "czech": "new"}

        petr = User(tg_id=2, name="Петя", topics=["czech"], muted_sources=[], minutes=10, delivery_time="08:00")
        db.add(petr)
        db.commit()
        ep = producer.assemble(db, s, petr, "2026-10-20")  # пришёл после утренней сборки — доозвучили его тему
        assert ep and [c["topic"] for c in ep.chapters[1:]] == ["czech"]
