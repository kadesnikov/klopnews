"""Весь конвейер без сети: тестовые новости → сюжеты (fake LLM) → клипы (dummy TTS) → выпуск → страница."""
from datetime import timedelta

from fastapi.testclient import TestClient

from klopnews import producer
from klopnews.auth import sign
from klopnews.config import settings
from klopnews.db import Item, Story, User, session, utcnow
from klopnews.render import studio


def test_full_pipeline(monkeypatch):
    monkeypatch.setattr("klopnews.feeds.article", lambda url: {"text": "", "image": None})
    monkeypatch.setattr("klopnews.markets.fetch_quotes", lambda ins: [
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
        assert {st.kind for st in stories} == {"story", "markets"}
        assert all(st.status == "ready" and st.duration > 1 for st in stories)
        assert sum(bool(st.reporter_script) for st in stories) == 2

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
    r = client.post("/settings", data={"name": "Мария", "minutes": "15", "delivery_time": "08:15",
                                       "topics": ["ai", "finance"], "sources": ["verge", "marketwatch"]})
    assert r.status_code == 200
    with session() as db:
        u = db.get(User, user.id)
        assert (u.name, u.minutes, u.delivery_time, u.topics) == ("Мария", 15, "08:15", ["ai", "finance"])
        assert "verge" not in u.muted_sources and "irozhlas" in u.muted_sources
