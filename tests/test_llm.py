import pytest

from klopnews.llm import extract_json


def test_extract_json_from_chatty_reply():
    assert extract_json('Вот:\n```json\n{"a": [1, {"b": "}"}]}\n```\nГотово') == {"a": [1, {"b": "}"}]}
    with pytest.raises(ValueError):
        extract_json("нет json")
