import numpy as np

from klopnews import tts
from klopnews.config import settings


def test_speed_change_reuses_cached_voice(monkeypatch):
    calls = []

    class Resp:
        content = (np.zeros(tts.RATE, dtype="<i2")).tobytes()

        def raise_for_status(self):
            pass

    monkeypatch.setenv("TTS_PROVIDER", "openai")
    monkeypatch.setattr("klopnews.tts.httpx.post", lambda *a, **k: calls.append(1) or Resp())
    for speed in ("1.15", "1.25", "1"):
        monkeypatch.setenv("TTS_SPEED", speed)
        settings.cache_clear()
        wav = tts.synthesize("Привет", "cedar", "бодро", settings())
        assert abs(len(tts.read_wav(wav)[0]) / tts.RATE - 1 / float(speed)) < 0.05
    assert len(calls) == 1
