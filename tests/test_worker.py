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
