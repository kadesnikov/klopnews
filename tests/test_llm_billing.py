from datetime import timedelta

import anthropic
import httpx
import pytest

from klopnews import llm, producer, worker
from klopnews.config import settings
from klopnews.db import Edition, Item, Story, User, session, utcnow


def api_error(status: int, message: str) -> anthropic.APIStatusError:
    resp = httpx.Response(status, request=httpx.Request("POST", "https://api.moonshot.ai/anthropic/v1/messages"))
    return anthropic.APIStatusError(message, response=resp, body=None)


@pytest.mark.parametrize("status, text, billing", [
    (429, "Your account org-1 is suspended, please check your plan and billing details", True),
    (429, "exceeded_current_quota_error: insufficient balance", True),
    (401, "Invalid Authentication", True),
    (402, "payment required", True),
    (429, "rate limit reached for requests", False),  # просто много запросов — SDK переждёт сам
    (400, "content_filter: high risk", False),
    (529, "overloaded", False),
])
def test_billing_problem(status, text, billing):
    assert llm.billing_problem(status, text) is billing


def test_complete_remembers_billing_problem(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("LLM_API_KEY", "k")
    settings.cache_clear()
    model = llm.AnthropicLLM(settings())
    monkeypatch.setattr(model.client.messages, "create",
                        lambda **kw: (_ for _ in ()).throw(api_error(429, "insufficient balance")))
    monkeypatch.setattr(llm, "problem", None)
    with pytest.raises(llm.LLMBillingError):
        model.complete("sys", "user")
    assert "kimi" in llm.problem and "деньги" in llm.problem


class BrokeLLM:
    def complete(self, system, user):
        llm.problem = "модель kimi: закончились деньги"
        raise llm.LLMBillingError(llm.problem)


def test_edition_without_money_is_not_ready_and_resumes(monkeypatch):
    monkeypatch.setattr("klopnews.feeds.article", lambda url: {"text": "", "image": None})
    monkeypatch.setattr("klopnews.markets.fetch_quotes", lambda ins: [])
    monkeypatch.setattr("klopnews.render.studio.render_segment", lambda *a, **k: 1.0)
    monkeypatch.setattr(producer, "make_llm", lambda s: BrokeLLM())
    s = settings()
    with session() as db:
        db.add(Item(source_id="verge", url="https://ex.com/1", title="OpenAI модель", summary="",
                    published_at=utcnow() - timedelta(hours=1)))
        db.add(User(tg_id=7, name="Маша", topics=[], muted_sources=[], minutes=5, delivery_time="07:30"))
        db.commit()
        with pytest.raises(llm.LLMBillingError):  # ошибки тем проглочены, но сборка всё равно видит беду
            producer.build_edition(db, s, "2026-11-13", utcnow())
        assert db.get(Edition, "2026-11-13").status == "failed"  # воркер попробует снова

        monkeypatch.setattr(producer, "make_llm", lambda s: llm.FakeLLM())  # счёт пополнили
        producer.build_edition(db, s, "2026-11-13", utcnow())
        assert db.get(Edition, "2026-11-13").status == "ready"
        assert db.query(Story).filter(Story.script != "").count() >= 1  # тема, сорвавшаяся без денег, дописана


def test_deliver_reports_billing_instead_of_nothing_found(monkeypatch):
    sent = []
    monkeypatch.setattr(worker.bot, "send", lambda *a, **k: sent.append(a) or True)

    def assemble(db, s, user, ed):
        llm.problem = "модель kimi: закончились деньги"
        return None

    monkeypatch.setattr(worker.producer, "assemble", assemble)
    with session() as db:
        user = User(tg_id=7, name="Маша", minutes=5, delivery_time="07:30")
        db.add(user)
        db.commit()
        with pytest.raises(llm.LLMBillingError):
            worker.deliver(db, settings(), user, "2026-11-13")
    assert sent == []  # «ничего не нашлось» человеку не уходит — уходит алерт админу через tick
