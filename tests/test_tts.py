import numpy as np

from klopnews import tts
from klopnews.config import settings


def test_speed_change_reuses_cached_voice(monkeypatch):
    calls = []

    class Resp:
        content = (np.zeros(tts.RATE, dtype="<i2")).tobytes()
        is_success = True

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


def _fake_post(monkeypatch, responses):
    import httpx
    from klopnews import tts
    calls = []

    def post(url, **kw):
        calls.append(url)
        code, body, headers = responses[min(len(calls), len(responses)) - 1]
        return httpx.Response(code, content=body, headers=headers, request=httpx.Request("POST", url))

    monkeypatch.setattr(tts.httpx, "post", post)
    monkeypatch.setattr(tts.time, "sleep", lambda s: None)
    return calls


def test_tts_waits_out_rate_limit(monkeypatch):
    from klopnews import tts
    calls = _fake_post(monkeypatch, [(429, b'{"error":{"code":"rate_limit_exceeded"}}', {"retry-after": "3"}),
                                     (200, b"\x00\x01" * 100, {})])
    assert tts._post("https://x/audio/speech").status_code == 200 and len(calls) == 2


def test_tts_fails_fast_when_out_of_money(monkeypatch):
    import pytest
    from klopnews import tts
    calls = _fake_post(monkeypatch, [(429, b'{"error":{"code":"insufficient_quota"}}', {})])
    with pytest.raises(tts.TTSError) as e:
        tts._post("https://x/audio/speech")
    assert len(calls) == 1 and not e.value.transient and "деньги" in str(e.value)


def test_tts_gives_up_after_retries_as_transient(monkeypatch):
    import pytest
    from klopnews import tts
    calls = _fake_post(monkeypatch, [(503, b"overloaded", {})])
    with pytest.raises(tts.TTSError) as e:
        tts._post("https://x/audio/speech")
    assert len(calls) == len(tts.RETRY_WAITS) + 1 and e.value.transient


def test_tts_does_not_retry_bad_request(monkeypatch):
    import pytest
    from klopnews import tts
    calls = _fake_post(monkeypatch, [(400, b"bad voice", {})])
    with pytest.raises(tts.TTSError) as e:
        tts._post("https://x/audio/speech")
    assert len(calls) == 1 and not e.value.transient
