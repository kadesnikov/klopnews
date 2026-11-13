import time

import pytest

from klopnews import worker
from klopnews.config import settings


@pytest.fixture
def sent(monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "100,200")
    settings.cache_clear()
    out = []
    monkeypatch.setattr(worker.bot, "send", lambda s, chat, text, button=None: out.append((chat, text)) or True)
    monkeypatch.setattr(worker, "ALERTED", set())
    return out


def test_alert_goes_to_every_admin_once(sent):
    s = settings()
    worker.alert(s, "tick:2026-11-13", "Выпуск <не> собирается")
    worker.alert(s, "tick:2026-11-13", "Выпуск <не> собирается")  # воркер повторяет каждую минуту — админ не спамится
    assert sorted(chat for chat, _ in sent) == [100, 200]
    assert "&lt;не&gt;" in sent[0][1]


def test_alert_survives_telegram_failure(sent, monkeypatch):
    monkeypatch.setattr(worker.bot, "send", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("telegram down")))
    worker.alert(settings(), "x", "текст")  # не падает


def test_failed_edition_alerts_admin_once(sent, monkeypatch):
    s = settings()
    monkeypatch.setattr(worker, "local_now", lambda s: worker.datetime(2026, 11, 13, 6, 0))
    monkeypatch.setattr(worker.feeds, "fetch_all", lambda src: 0)
    monkeypatch.setattr(worker, "ensure_edition", lambda db, s: (_ for _ in ()).throw(RuntimeError("LLM 529")))
    for _ in range(3):  # три минуты подряд сборка падает
        assert worker.safe_tick(s, time.time()) > 0
    assert len(sent) == 2 and "LLM 529" in sent[0][1] and "2026-11-13" in sent[0][1]
    assert worker._busy_since is None


def test_watchdog_reports_stuck_tick(sent, monkeypatch):
    calls = iter(range(2))
    monkeypatch.setattr(worker.time, "sleep", lambda _: next(calls))  # два оборота и выход по StopIteration
    monkeypatch.setattr(worker, "_busy_since", time.time() - worker.STUCK_AFTER - 60)
    with pytest.raises(StopIteration):
        worker.watchdog(settings())
    assert len(sent) == 2 and "завис" in sent[0][1]


def test_watchdog_quiet_between_ticks(sent, monkeypatch):
    calls = iter(range(2))
    monkeypatch.setattr(worker.time, "sleep", lambda _: next(calls))
    monkeypatch.setattr(worker, "_busy_since", None)
    with pytest.raises(StopIteration):
        worker.watchdog(settings())
    assert sent == []


def test_failed_delivery_is_retried_later(sent, monkeypatch):
    from klopnews.db import User, session
    s = settings()
    with session() as db:
        db.add(User(tg_id=7, name="Маша", minutes=5, delivery_time="07:30"))
        db.commit()
    clock = [1000.0]
    monkeypatch.setattr(worker.time, "time", lambda: clock[0])
    monkeypatch.setattr(worker, "TRIED", {})
    monkeypatch.setattr(worker, "local_now", lambda s: worker.datetime(2026, 11, 13, 7, 31))
    monkeypatch.setattr(worker.feeds, "fetch_all", lambda src: 0)
    monkeypatch.setattr(worker, "ensure_edition", lambda db, s: "2026-11-13")
    attempts = []

    def deliver(db, s, user, ed):
        attempts.append(clock[0])
        if len(attempts) < 3:
            raise RuntimeError("429")
        db.add(worker.Episode(user_id=user.id, edition=ed))  # получилось — выпуск есть, больше не трогаем
        db.commit()

    monkeypatch.setattr(worker, "deliver", deliver)
    for minute in range(60):  # час работы воркера
        clock[0] = 1000.0 + minute * 60
        worker.tick(s, clock[0])
    assert [round((t - 1000) / 60) for t in attempts] == [0, 10, 30]  # сразу, через 10 и ещё через 20 мин
    assert len(sent) == 2  # один алерт админам (их двое), без спама на каждой попытке
