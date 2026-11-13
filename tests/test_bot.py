import pytest

from klopnews import bot

TOPICS = {"weather": {"title": "Погода"}, "ai": {"title": "ИИ и технологии"}, "world": {"title": "В мире"},
          "finance": {"title": "Рынки и деньги"}}
INTRO = {"t": 0, "title": "Начало выпуска", "topic": ""}


def test_digest_groups_by_topic_and_escapes():
    chapters = [INTRO,
                {"t": 10, "title": "OpenAI & <Co>", "topic": "ai", "bullets": ["Раз", "Два"]},
                {"t": 40, "title": "Ещё про ИИ", "topic": "ai"},
                {"t": 70, "title": "Саммит", "topic": "world", "bullets": ["Итог"]}]
    text = bot.digest(chapters, TOPICS)
    assert text == ("<blockquote expandable><b>ИИ и технологии</b>\n• OpenAI &amp; &lt;Co&gt;\n<i>Раз · Два</i>\n"
                    "• Ещё про ИИ\n\n<b>В мире</b>\n• Саммит\n<i>Итог</i></blockquote>")


def test_digest_skips_header_for_self_titled_rubrics():
    chapters = [INTRO, {"t": 5, "title": "Погода: Прага, +6", "topic": "weather", "kind": "weather"},
                {"t": 9, "title": "Рынки за день", "topic": "finance", "kind": "markets"}]
    text = bot.digest(chapters, TOPICS)
    assert "<b>Погода</b>" not in text and "<b>Рынки и деньги</b>" not in text
    assert text.startswith("<blockquote expandable>• Погода: Прага")  # свёрнутое превью начинается с сюжета
    assert "+6\n\n• Рынки" in text  # разные темы разделены даже без шапки


def test_digest_cuts_whole_stories_and_counts_the_rest():
    chapters = [INTRO] + [{"t": i, "title": f"Заголовок {i} " * 8, "topic": "ai", "bullets": ["пункт " * 8] * 3}
                          for i in range(40)]
    text = bot.digest(chapters, TOPICS)
    assert len(text) < 4096
    shown = text.count("• ")
    assert text.count("<i>") == shown  # ни один сюжет не обрезан посередине
    assert text.endswith(f"…и ещё {40 - shown} сюжетов — в выпуске</blockquote>")


def test_digest_empty_for_old_episode():
    assert bot.digest([INTRO], TOPICS) == ""


@pytest.mark.parametrize("n, word", [(1, "сюжет"), (2, "сюжета"), (5, "сюжетов"), (11, "сюжетов"),
                                     (14, "сюжетов"), (21, "сюжет"), (22, "сюжета"), (111, "сюжетов")])
def test_plural(n, word):
    assert bot.plural(n, "сюжет", "сюжета", "сюжетов") == word


def test_notify_falls_back_to_plain_message(monkeypatch):
    from types import SimpleNamespace
    from klopnews.config import settings
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    settings.cache_clear()
    sent = []

    def api(s, method, **p):  # Telegram отвергает сообщение с цитатой
        sent.append(p["text"])
        return {"ok": "blockquote" not in p["text"]}

    monkeypatch.setattr(bot, "api", api)
    ep = SimpleNamespace(duration=480, slug="abc", chapters=[INTRO, {"t": 5, "title": "Новость", "topic": "ai"}])
    bot.notify_episode(settings(), SimpleNamespace(id=1, tg_id=7), ep, 1)
    assert len(sent) == 2 and "blockquote" not in sent[1] and "1 сюжет · 8 мин" in sent[1]


def test_notify_raises_when_nothing_was_delivered(monkeypatch):
    from types import SimpleNamespace
    from klopnews.config import settings
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    settings.cache_clear()
    monkeypatch.setattr(bot, "api", lambda s, m, **p: {"ok": False})
    ep = SimpleNamespace(duration=60, slug="abc", chapters=[INTRO])
    with pytest.raises(RuntimeError):
        bot.notify_episode(settings(), SimpleNamespace(id=1, tg_id=7), ep, 0)
