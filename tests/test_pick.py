from klopnews.db import Story, User
from klopnews.producer import intro_script, pick_stories


def st(topic, imp, dur, kind="story", src="a"):
    return Story(edition="2026-10-20", topic=topic, kind=kind, importance=imp, headline=f"{topic}{imp}", script="",
                 sources=[{"id": src, "name": src, "url": "u"}] if kind != "markets" else [], duration=dur, status="ready")


def test_pick_respects_topics_muted_sources_and_budget():
    stories = [st("ai", 9, 60), st("ai", 3, 20, "brief"), st("world", 8, 60, src="muted"),
               st("finance", 7, 50, "markets"), st("science", 6, 60), st("ai", 7, 60)]
    user = User(name="Маша", topics=["ai", "finance", "world"], muted_sources=["muted"], delivery_time="07:30")
    picked = pick_stories(user, stories, budget=175)
    assert [p.headline for p in picked] == ["ai9", "ai7", "finance7"]  # ai3 не влез, world — заглушён
    picked = pick_stories(user, stories, budget=1000)
    assert [p.kind for p in picked] == ["story", "story", "markets", "brief"]


def test_all_topics_when_empty_and_skip_failed():
    stories = [st("science", 6, 60), st("ai", 5, 60)]
    stories[1].status = "failed"
    assert [p.topic for p in pick_stories(User(topics=[], muted_sources=[]), stories, 600)] == ["science"]


def test_intro_greets_by_time():
    u = User(name="Маша", delivery_time="19:00")
    text = intro_script(u, [st("ai", 9, 60), st("ai", 8, 60)])
    assert text.startswith("Добрый вечер, Маша!") and "ai9 и ai8" in text
