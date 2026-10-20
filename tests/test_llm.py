import pytest

from klopnews.llm import extract_json


def test_extract_json_from_chatty_reply():
    assert extract_json('Вот:\n```json\n{"a": [1, {"b": "}"}]}\n```\nГотово') == {"a": [1, {"b": "}"}]}
    with pytest.raises(ValueError):
        extract_json("нет json")


def test_select_splits_around_filtered_item():
    from klopnews.db import Item
    from klopnews.editor import select_events
    from klopnews.llm import FakeLLM

    class Picky(FakeLLM):
        def complete(self, system, user):
            if "запрещёнка" in user:
                raise RuntimeError("Error code: 400 - {'type': 'content_filter'}")
            return super().complete(system, user)

    items = [Item(source_id="s", title=t, summary="", url=t) for t in ["a", "b", "запрещёнка", "c"]]
    events = select_events(Picky(), "Тема", items, {"s": {"name": "S"}}, [], 8)
    picked = {it.title for ev in events for it in ev["items"]}
    assert "запрещёнка" not in picked and picked >= {"a", "c"}
