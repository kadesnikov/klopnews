"""Журнал редакции: что модель видела, что выбрала и что отрезало окно."""
from datetime import timedelta

from fastapi.testclient import TestClient

from klopnews import config, producer
from klopnews.auth import sign
from klopnews.db import Item, Selection, User, session, utcnow


def test_desk_records_what_model_saw_and_picked(monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "1")
    config.settings.cache_clear()
    monkeypatch.setattr("klopnews.feeds.article", lambda url: {"text": "", "image": None})
    monkeypatch.setattr("klopnews.markets.fetch_quotes", lambda ins: [])
    monkeypatch.setattr("klopnews.render.studio.render_segment", lambda *a, **k: 1.0)
    s = config.settings()
    with session() as db:
        # 65 новостей ИИ: 60 самых свежих модель увидит, 5 самых старых не влезут в окно
        for i in range(65):
            title = "Microsoft Outlook is down" if i == 64 else f"Новость про ИИ {i}"
            db.add(Item(source_id="verge", url=f"https://ex.com/{i}", title=title, summary="",
                        published_at=utcnow() - timedelta(minutes=10 * i + 1)))
        db.add(User(tg_id=1, name="Папа", topics=[], muted_sources=[], minutes=5))
        db.commit()
        producer.build_edition(db, s, "2026-11-13", utcnow())
        ai = db.query(Selection).filter(Selection.edition == "2026-11-13", Selection.topic == "ai").one()
        assert len(ai.shown) == 60 and ai.error is None
        assert [c["why"] for c in ai.cut] == ["limit"] * 5
        assert ai.cut[-1]["title"] == "Microsoft Outlook is down"  # самая старая — первой вылетает из окна
        assert ai.picked and all(ev["items"] for ev in ai.picked)  # FakeLLM выбирает первые 4

    from klopnews.web import app

    client = TestClient(app)
    assert client.get("/admin/desk").status_code == 404  # без входа
    client.get(f"/login?t={sign(1, 60, s.session_secret)}")
    page = client.get("/admin/desk?date=2026-11-13")
    assert page.status_code == 200
    assert "ИИ и технологии" in page.text and "модель видела 60" in page.text
    assert "5 не влезли в окно" in page.text and "Microsoft Outlook is down" in page.text
    assert "Видела, но не выбрала" in page.text
    empty = client.get("/admin/desk?date=2020-01-01")
    assert empty.status_code == 200 and "журнала нет" in empty.text
    assert client.get("/admin/desk?date=../../etc").status_code == 200  # мусор в дате — просто сегодняшний день


def test_desk_records_model_failure(monkeypatch):
    monkeypatch.setattr("klopnews.feeds.article", lambda url: {"text": "", "image": None})
    monkeypatch.setattr("klopnews.markets.fetch_quotes", lambda ins: [])

    class Broken:
        def complete(self, system, user):
            raise RuntimeError("модель упала")

    monkeypatch.setattr(producer, "make_llm", lambda s: Broken())
    with session() as db:
        db.add(Item(source_id="verge", url="https://ex.com/x", title="Новость", summary="",
                    published_at=utcnow() - timedelta(hours=1)))
        db.commit()
        producer.build_edition(db, config.settings(), "2026-11-13", utcnow())
        sel = db.query(Selection).filter(Selection.topic == "ai").one()
        assert "модель упала" in sel.error and sel.picked == [] and len(sel.shown) == 1
