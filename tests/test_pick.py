from datetime import date

from klopnews.db import Story, User
from klopnews.producer import intro_script, pick_stories, to_voice


def st(topic, imp, dur, kind="story", src="a"):
    return Story(edition="2026-10-20", topic=topic, kind=kind, importance=imp, headline=f"{topic}{imp}", script="",
                 sources=[{"id": src, "name": src, "url": f"u/{topic}{imp}"}] if kind != "markets" else [], duration=dur, status="ready")


def test_pick_respects_topics_muted_sources_and_budget():
    stories = [st("ai", 9, 60), st("ai", 3, 20, "brief"), st("world", 8, 60, src="muted"),
               st("finance", 7, 50, "markets"), st("science", 6, 60), st("ai", 7, 60)]
    user = User(name="Маша", topics=["ai", "finance", "world"], muted_sources=["muted"], delivery_time="07:30")
    picked = pick_stories(user, stories, budget=160)
    assert [p.headline for p in picked] == ["ai9", "ai7", "finance7"]  # 170 с: перебор в 20% можно, ai3 уже лишний
    picked = pick_stories(user, stories, budget=175)
    assert [p.headline for p in picked] == ["ai9", "ai7", "finance7", "ai3"]  # world — заглушён
    picked = pick_stories(User(name="Петя", topics=["ai"], muted_sources=[]), stories, budget=1000)
    assert "markets" not in [p.kind for p in picked]  # тему «Рынки и деньги» отключил — рынков нет
    picked = pick_stories(user, stories, budget=1000)
    assert [p.kind for p in picked] == ["story", "story", "markets", "brief"]


def test_all_topics_when_empty_and_skip_failed():
    stories = [st("science", 6, 60), st("ai", 5, 60)]
    stories[1].status = "failed"
    assert [p.topic for p in pick_stories(User(topics=[], muted_sources=[]), stories, 600)] == ["science"]


def test_intro_greets_by_time():
    u = User(name="Маша", delivery_time="19:00")
    text = intro_script(u, [st("ai", 9, 60), st("ai", 8, 60)], date(2026, 10, 21))
    assert text.startswith("Добрый вечер, Маша!") and "ai9 и ai8" in text and "рождения" not in text


def test_intro_birthday_and_holiday():
    u = User(name="Маша", delivery_time="07:30", birthday="1990-10-31")
    text = intro_script(u, [st("ai", 9, 60)], date(2026, 10, 31))
    assert "Маша, с днём рождения!" in text and "Хеллоуин" in text


def test_same_event_twice_is_shown_once():
    stories = [st("finance", 9, 30), st("finance", 8, 30), st("finance", 7, 30)]
    stories[0].headline = "Владелец GCash вышел на биржу с рекордным IPO на Филиппинах"
    stories[1].headline = "Владелец GCash готов покупать конкурентов после IPO"
    stories[2].headline = "Maersk строит железнодорожную сеть во Вьетнаме"
    picked = pick_stories(User(topics=[], muted_sources=[]), stories, 600)
    assert [p.importance for p in picked] == [9, 7]


def test_markets_always_fit_and_same_article_twice_is_dropped():
    stories = [st("ai", 9, 60), st("science", 8, 60), st("finance", 7, 50, "markets")]
    stories[0].headline, stories[1].headline = "Quantinuum привлекла инвесторов", "Квантовый компьютер побил рекорд"
    stories[1].sources = stories[0].sources  # одна статья Ars в двух темах
    picked = pick_stories(User(topics=[], muted_sources=[]), stories, budget=115)
    assert [p.kind for p in picked] == ["story", "markets"]


def test_voice_only_what_fits_someones_episode():
    def new(topic, imp, words):
        x = st(topic, imp, 0)
        x.status, x.script = "new", "слово " * words
        return x

    stories = [new("ai", 9, 270), new("ai", 8, 270), new("ai", 7, 270), new("world", 9, 270), new("science", 9, 270)]
    stories[1].status, stories[1].duration = "ready", 100  # уже озвучен — второй раз не нужен
    users = [User(topics=["ai"], muted_sources=[], minutes=4), User(topics=["world"], muted_sources=[], minutes=4)]
    # по ~101 с на сюжет: 4 минуты — 205 с, с запасом 246 с и допуском +20% — 295 с: два ai влезают, третий — нет;
    # science не выбрал никто
    assert [x.headline for x in to_voice(users, stories, 1.2)] == ["ai9", "world9"]
