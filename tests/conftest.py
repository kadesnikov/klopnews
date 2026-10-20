import pytest

from klopnews import config


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("TTS_PROVIDER", "dummy")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    config.settings.cache_clear()
    yield
    config.settings.cache_clear()
