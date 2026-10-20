from fastapi.testclient import TestClient

from klopnews import config
from klopnews.auth import sign
from klopnews.db import Edition, Episode, User, View, session


def test_watch_pings_and_admin_page(monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "1")
    config.settings.cache_clear()
    s = config.settings()
    with session() as db:
        boss, masha = User(tg_id=1, name="Папа"), User(tg_id=7, name="Маша", topics=["ai"])
        db.add_all([boss, masha, Edition(date="2026-10-20", status="ready")])
        db.commit()
        ep = Episode(user_id=masha.id, edition="2026-10-20", duration=100.0,
                     chapters=[{"t": 0, "title": "Интро"}, {"t": 10, "title": "ИИ", "topic": "ai"},
                               {"t": 60, "title": "Рынки", "topic": "finance"}])
        db.add(ep)
        db.commit()

    from klopnews.web import app

    client = TestClient(app)
    w = f"/e/{ep.slug}/watch"
    assert client.post(w, json={"k": "abcdefgh12", "src": "tg"}).status_code == 204  # открыла по ссылке из бота
    client.post(w, json={"k": "abcdefgh12", "t": 70, "p": 65, "src": "tg"})
    client.post(w, json={"k": "abcdefgh12", "t": 30, "p": 40})  # старый пинг не откатывает прогресс
    client.post(w, json={"k": "guest00001", "t": 20, "p": 20})  # кто-то по пересланной ссылке
    assert client.post(w, json={"k": "../bad"}).status_code == 400
    with session() as db:
        own = db.query(View).filter(View.key == "abcdefgh12").one()
        assert (own.viewer_id, own.max_t, own.played) == (ep.user_id, 70, 65)
        assert db.query(View).filter(View.key == "guest00001").one().viewer_id is None

    assert client.get("/admin").status_code == 404
    client.get(f"/login?t={sign(ep.user_id, 60, s.session_secret)}")
    assert client.get("/admin").status_code == 404  # Маша не админ
    client.get(f"/login?t={sign(1, 60, s.session_secret)}")
    page = client.get("/admin")
    assert page.status_code == 200
    assert "Маша" in page.text and "65%" in page.text and "Где бросают" in page.text and "Админка" in page.text
