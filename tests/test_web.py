from datetime import datetime

from fastapi.testclient import TestClient

from klopnews import bot, config
from klopnews.auth import sign
from klopnews.db import Episode, User, View, session


def test_home_features_latest_episode_and_watch_progress(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "KlopNewsBot")
    config.settings.cache_clear()
    s = config.settings()
    chapters = [{"t": 0, "title": "Интро"}, {"t": 10, "title": "Модели для кода", "topic": "ai"},
                {"t": 60, "title": "Метро D", "topic": "czech"}]
    with session() as db:
        masha = User(tg_id=7, name="Маша")
        db.add(masha)
        db.commit()
        old, half, new = (Episode(user_id=masha.id, edition=f"2026-10-{d}", duration=100.0, chapters=chapters,
                                  created_at=datetime(2026, 10, d)) for d in (10, 11, 12))
        db.add_all([old, half, new])
        db.commit()
        db.add_all([View(key="aaaaaaaa01", episode_id=old.id, viewer_id=masha.id, max_t=100),
                    View(key="aaaaaaaa02", episode_id=half.id, viewer_id=masha.id, max_t=40),
                    View(key="aaaaaaaa03", episode_id=half.id, viewer_id=None, max_t=90)])  # гость не считается
        db.commit()
        uid = masha.id

    from klopnews.web import app

    client = TestClient(app)
    client.get(f"/login?t={sign(uid, 60, s.session_secret)}")
    html = client.get("/").text
    assert "Последний выпуск" in html and "2 сюжета" in html and "Метро D" in html
    assert html.count('class="new"') == 0  # оба прошлых выпуска уже смотрели
    assert "досмотрен</span>" in html and ">40%</span>" in html
    assert 'class="studio"' in html  # картинки нет — заставка с ведущими вместо битого превью


def test_expired_link_offers_bot_deep_link(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "KlopNewsBot")
    config.settings.cache_clear()
    from klopnews.web import app

    html = TestClient(app).get("/?expired=1").text
    assert "Ссылка для входа устарела" in html and "https://t.me/KlopNewsBot?start=login" in html


def test_start_login_sends_fresh_link(monkeypatch):
    sent = []
    monkeypatch.setattr(bot, "send", lambda s, chat, text, button=None: sent.append((text, button)))
    bot.handle(config.settings(), {"chat": {"id": 5}, "from": {"id": 5, "first_name": "Нина"}, "text": "/start login"},
               make_now=None)
    (text, (label, url)), = sent
    assert "свежая ссылка" in text and "/login?t=" in url and url.endswith("&next=/")
