import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from klopnews import editor, events, producer
from klopnews.config import catalog, settings
from klopnews.db import Event, Story, User, session

CFG = {"city": "Praha", "timezone": "Europe/Prague", "pages": ["https://afisha.example/ai"], "searches": []}
NOW = datetime(2026, 10, 20, 8, 0)  # вторник, UTC


class StubLLM:
    def __init__(self, found):
        self.found, self.calls = found, 0

    def complete(self, system, user):
        self.calls += 1
        if '"afisha"' in system:
            return json.dumps({"script": "Сходите на митап."})
        return json.dumps({"found": self.found})


def test_normalize_window_and_dates():
    zone = ZoneInfo("Europe/Prague")
    start = NOW.replace(tzinfo=timezone.utc)
    raw = {"found": [
        {"title": "AI Tinkerers", "starts_at": "2026-10-23T19:00 (local time)", "free": True, "url": "https://x/1"},
        {"title": "Лекция без времени", "starts_at": "2026-10-20", "url": ""},  # сегодня, без часов — остаётся
        {"title": "Прошлое", "starts_at": "2026-10-19T19:00"},
        {"title": "Далеко", "starts_at": "2026-11-20T19:00"},
        {"title": "", "starts_at": "2026-10-22T19:00"},
        {"title": "Без даты"},
    ]}
    out = events.normalize(raw, start, start + timedelta(days=14), "https://src", zone)
    assert [x["title"] for x in out] == ["AI Tinkerers", "Лекция без времени"]
    assert out[0]["starts_at"] == datetime(2026, 10, 23, 17, 0) and out[0]["has_time"]
    assert not out[1]["has_time"] and out[1]["url"] == "https://src"


def test_junk_filter():
    assert events.junk("Meetup about LLM agents in Prague") is None
    assert events.junk("Přednáška: umělá inteligence ve škole") is None
    assert events.junk("Koncert v Rudolfinu, jazz") == "no_ai"
    assert events.junk("404 Not Found") == "not_found"
    assert events.junk("Digital marketing workshop, capital markets") == "no_ai"


def test_collect_dedupes_and_skips_unchanged(monkeypatch):
    monkeypatch.setattr("klopnews.events.fetch", lambda client, url: "AI meetup page v1")
    found = [{"title": "AI Tinkerers", "starts_at": "2026-10-23T19:00", "url": "https://x/1"},
             {"title": "AI Tinkerers", "starts_at": "2026-10-23T19:00", "url": "https://x/1"}]
    llm = StubLLM(found)
    with session() as db:
        assert events.collect(db, settings(), llm, CFG, NOW, client=object()) == 1
        assert events.collect(db, settings(), llm, CFG, NOW + timedelta(hours=1), client=object()) == 0
        assert llm.calls == 1  # страница не изменилась — модель второй раз не читает
        assert db.query(Event).count() == 1


def test_announce_new_then_monday_overview():
    with session() as db:
        db.add(Event(key="a", title="🦞 AI Tinkerers", starts_at=datetime(2026, 10, 23, 17), url="https://x/1"))
        db.add(Event(key="b", title="Вчерашний", starts_at=datetime(2026, 10, 19, 17), url="https://x/2"))
        db.commit()
        evs = events.to_announce(db, NOW, CFG)
        assert [e.title for e in evs] == ["🦞 AI Tinkerers"]
        evs[0].announced = True
        db.commit()
        assert events.to_announce(db, NOW, CFG) == []  # нового нет, не понедельник
        monday = datetime(2026, 10, 19, 8, 0)
        db.add(Event(key="c", title="ML Prague", starts_at=datetime(2026, 10, 24, 8), url="https://x/3", announced=True))
        db.commit()
        assert [e.title for e in events.to_announce(db, monday, CFG)] == ["Вчерашний", "🦞 AI Tinkerers", "ML Prague"]
        st = editor.write_events(StubLLM([]), events.to_announce(db, monday, CFG), CFG, "2026-10-19")
        assert st.kind == "events" and st.topic == "ai" and st.bullets[1] == "пт 23.10 · AI Tinkerers"
        assert len(st.sources) == 3


def test_afisha_block_in_episode(monkeypatch):
    monkeypatch.setattr("klopnews.markets.fetch_quotes", lambda ins: [])
    monkeypatch.setattr("klopnews.render.studio.render_segment", lambda *a, **k: 30.0)
    s = settings()
    now = datetime(2026, 10, 20, 4, 0)
    with session() as db:
        db.add(Event(key="a", title="AI Tinkerers", starts_at=datetime(2026, 10, 23, 17), url="https://x/1"))
        db.commit()
        producer.build_edition(db, s, "2026-10-20", now)
        st = db.query(Story).filter(Story.kind == "events").one()
        assert st.status == "ready" and db.query(Event).one().announced
        producer.build_edition(db, s, "2026-10-20", now)  # перезапуск воркера — второго блока нет
        assert db.query(Story).filter(Story.kind == "events").count() == 1
        stories = db.query(Story).all()
        ai = User(tg_id=1, name="Маша", topics=["ai"], muted_sources=["verge"], minutes=10)
        other = User(tg_id=2, name="Папа", topics=["finance"], minutes=10)
        assert st in producer.pick_stories(ai, stories, 600)
        assert st not in producer.pick_stories(other, stories, 600)
    assert "events" in catalog() and catalog()["events"]["pages"]
