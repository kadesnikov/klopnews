from klopnews import bot

TOPICS = {"ai": {"title": "ИИ и технологии"}, "world": {"title": "В мире"}}


def test_digest_groups_by_topic_and_escapes():
    chapters = [{"t": 0, "title": "Начало выпуска", "topic": ""},
                {"t": 10, "title": "OpenAI & <Co>", "topic": "ai", "bullets": ["Раз", "Два"]},
                {"t": 40, "title": "Ещё про ИИ", "topic": "ai"},
                {"t": 70, "title": "Саммит", "topic": "world", "bullets": ["Итог"]}]
    text = bot.digest(chapters, TOPICS)
    assert text.startswith("<blockquote expandable>") and text.endswith("</blockquote>")
    assert text.count("ИИ И ТЕХНОЛОГИИ") == 1 and "В МИРЕ" in text
    assert "OpenAI &amp; &lt;Co&gt;" in text and "Раз · Два" in text
    assert "Начало выпуска" not in text


def test_digest_fits_telegram_limit():
    chapters = [{"t": 0, "title": "", "topic": ""}] + [
        {"t": i, "title": "Заголовок " * 10, "topic": "ai", "bullets": ["пункт " * 8] * 3} for i in range(40)]
    text = bot.digest(chapters, TOPICS)
    assert len(text) < 4096 and text.endswith("…</blockquote>")


def test_digest_empty_for_old_episode():
    assert bot.digest([{"t": 0, "title": "Начало выпуска", "topic": ""}], TOPICS) == ""
