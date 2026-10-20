"""Модель за одной функцией ask_json(). Провайдер — в .env (как в piano-piano).

kimi      — Moonshot Kimi через Anthropic-совместимый endpoint
anthropic — Claude напрямую
fake      — без ключа: предсказуемые ответы, чтобы гонять конвейер локально
"""
import json
import logging
import re

from klopnews.config import Settings

log = logging.getLogger(__name__)
KIMI_BASE_URL = "https://api.moonshot.ai/anthropic"


def extract_json(text: str):
    """Первый JSON-объект из ответа: модели любят оборачивать его в ```json и пояснения."""
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON in reply")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


class AnthropicLLM:
    def __init__(self, s: Settings):
        import anthropic

        base_url = s.llm_base_url or (KIMI_BASE_URL if s.llm_provider == "kimi" else None)
        self.client = anthropic.Anthropic(api_key=s.llm_api_key, base_url=base_url, timeout=180)
        self.model, self.max_tokens = s.llm_model, s.llm_max_tokens
        self.extra = {"thinking": {"type": "disabled"}} if s.llm_provider == "kimi" else {}

    def complete(self, system: str, user: str) -> str:
        resp = self.client.messages.create(model=self.model, max_tokens=self.max_tokens, system=system,
                                           messages=[{"role": "user", "content": user}], **self.extra)
        return "".join(b.text for b in resp.content if b.type == "text")


class FakeLLM:
    def complete(self, system: str, user: str) -> str:
        if '"afisha"' in system:
            return json.dumps({"script": "На этой неделе в Праге есть куда сходить: митап про ИИ и лекция. Одобряю!"},
                              ensure_ascii=False)
        if '"found"' in system:
            return json.dumps({"found": []})
        if '"events"' in system:
            n = len(re.findall(r"^\[\d+\]", user, re.M))
            return json.dumps({"events": [{"items": [i], "importance": 8 - i, "reporter_ok": i == 0}
                                          for i in range(min(n, 4))]})
        if '"markets"' in system:
            return json.dumps({"script": "На рынках спокойно: индексы почти не изменились, золото держится у максимумов.",
                               "bullets": ["Индексы почти без изменений", "Золото у максимумов"]})
        title = re.search(r"Заголовок: (.+)", user)
        title = title.group(1) if title else "Новость"
        return json.dumps({
            "headline": title[:80], "tag": "ТЕСТ",
            "bullets": ["Первый важный факт", "Второй важный факт", "Что это значит для нас"],
            "script": f"{title}. Это тестовый сюжет без модели. Здесь будет рассказ на минуту. Мнение Клопа: одобряю.",
            "anchor_lead": "Подробности у нашего корреспондента. Клопик, тебе слово!",
            "reporter_script": "Клоп Александрович, я на месте событий! Тут всё очень интересно. Возвращаю слово в студию!",
            "place": "Прага",
        }, ensure_ascii=False)


def make_llm(s: Settings):
    return FakeLLM() if s.llm_provider == "fake" else AnthropicLLM(s)


def ask_json(llm, system: str, user: str, retries: int = 1) -> dict:
    for attempt in range(retries + 1):
        text = llm.complete(system, user)
        try:
            return extract_json(text)
        except ValueError as e:
            log.warning("bad_json attempt=%d: %r :: %s", attempt, e, text[:300])
    raise ValueError("model did not return JSON")
