from klopnews.auth import sign, verify


def test_roundtrip_and_tamper():
    t = sign(42, 60, "s")
    assert verify(t, "s") == 42
    assert verify(t, "other") is None
    assert verify(t.replace("42.", "43.", 1), "s") is None
    assert verify(sign(42, -1, "s"), "s") is None
    assert verify("garbage", "s") is None and verify(None, "s") is None


def test_production_requires_secret_and_allowlist():
    from klopnews.config import Settings

    dev = Settings(_env_file=None, public_url="http://localhost:8000", session_secret="")
    assert dev.production_problems() == []
    prod = dict(_env_file=None, public_url="https://news.example.com")
    assert len(Settings(**prod, session_secret="", allowed_telegram_ids="").production_problems()) == 2
    assert Settings(**prod, session_secret="dev-secret-change-me", allowed_telegram_ids="1").production_problems()
    assert Settings(**prod, session_secret="x" * 32, allowed_telegram_ids="1").production_problems() == []


def test_now_cooldown(monkeypatch):
    from klopnews import bot
    from klopnews.config import Settings

    sent, built = [], []
    monkeypatch.setattr(bot, "send", lambda s, chat, text, button=None: sent.append(text))
    monkeypatch.setattr(bot, "_last_now", {})
    monkeypatch.setattr(bot.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    s = Settings(admin_telegram_ids="9")
    msg = lambda uid: {"chat": {"id": uid}, "from": {"id": uid, "first_name": "Маша"}, "text": "/now"}  # noqa: E731
    bot.handle(s, msg(7), built.append)
    bot.handle(s, msg(7), built.append)  # второй раз сразу — не собираем
    assert "Следующий — через 30 мин" in sent[-1]
    bot.handle(s, msg(9), built.append)
    bot.handle(s, msg(9), built.append)  # админу можно
    monkeypatch.setattr(bot, "_last_now", {k: v - bot.NOW_COOLDOWN for k, v in bot._last_now.items()})
    bot.handle(s, msg(7), built.append)  # прошло полчаса
    assert len(built) == 4
