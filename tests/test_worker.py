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
    monkeypatch.setattr(worker, "local_now", lambda s: worker.datetime(2026, 11, 13, 7, 0))
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


def test_auto_edition_starts_an_hour_before_first_delivery(monkeypatch):
    from klopnews.db import User, session
    s = settings()
    assert s.edition_time == "auto"
    with session() as db:
        assert worker.edition_start(db, s) == "06:30"  # никого нет — от доставки по умолчанию 07:30
        db.add_all([User(tg_id=7, name="Маша", delivery_time="08:15"), User(tg_id=8, name="Папа", delivery_time="06:45")])
        db.commit()
        assert worker.edition_start(db, s) == "05:45"
    monkeypatch.setenv("EDITION_TIME", "05:30")
    settings.cache_clear()
    with session() as db:
        assert worker.edition_start(db, settings()) == "05:30"  # явное время из .env по-прежнему главное


def test_feeds_are_fresh_right_before_edition(monkeypatch):
    s = settings()
    fetched = []
    monkeypatch.setattr(worker.feeds, "fetch_all", lambda src: fetched.append(1))
    monkeypatch.setattr(worker, "ensure_edition", lambda db, s: "2026-11-13")
    monkeypatch.setattr(worker, "local_now", lambda s: worker.datetime(2026, 11, 13, 6, 31))
    worker.tick(s, time.time() - 15 * 60)  # плановый сбор был 15 минут назад
    assert fetched == [1]  # сюжеты дня ещё не собраны — ленты обновляются перед сборкой
    monkeypatch.setattr(worker, "local_now", lambda s: worker.datetime(2026, 11, 13, 6, 0))
    worker.tick(s, time.time() - 15 * 60)
    assert fetched == [1]  # до сборки — обычный ритм раз в два часа
