"""Озвучка в WAV с кэшем на диске по хешу (модель, голос, инструкция, текст).

openai     — любой OpenAI-совместимый /audio/speech, сырой PCM 24 кГц (gpt-4o-mini-tts понимает instructions)
elevenlabs — ElevenLabs, голос = voice_id; лучший русский, но дороже
dummy      — без ключа: «бу-бу-бу» по слогам, чтобы рот Клопа двигался при локальной разработке

Все модели читают медленнее телеведущих, поэтому дорожка ускоряется на tts_speed без смены тона.
"""
import hashlib
import logging
import subprocess
import time
import wave
from pathlib import Path

import httpx
import numpy as np

from klopnews.config import Settings

log = logging.getLogger(__name__)
RATE = 24000  # gpt-4o-mini-tts отдаёт 24 кГц моно
RETRY_WAITS = [5, 15, 30, 60, 120]  # 429 по лимиту запросов и 5xx проходят за секунды–минуты


class TTSError(RuntimeError):
    """Озвучка не удалась. transient=True — сбой временный, сюжет стоит озвучить позже."""

    def __init__(self, message: str, transient: bool):
        super().__init__(message)
        self.transient = transient


def _post(url: str, **kw) -> httpx.Response:
    """Запрос к озвучке с повторами: лимит запросов и сбои сервера пережидаем, кончившиеся деньги — нет."""
    for attempt, wait in enumerate([*RETRY_WAITS, None], 1):
        try:
            r = httpx.post(url, timeout=180, **kw)
        except httpx.TransportError as e:
            problem, transient = f"сеть: {e!r}", True
        else:
            if r.is_success:
                return r
            body = r.text[:300]
            if r.status_code == 429 and "insufficient_quota" in body:
                raise TTSError(f"TTS: закончились деньги на счёте ({r.status_code}): {body}", transient=False)
            transient = r.status_code == 429 or r.status_code >= 500
            problem = f"HTTP {r.status_code}: {body}"
            if transient and wait is not None:
                wait = max(wait, min(float(r.headers.get("retry-after", 0) or 0), 300))
        if not transient or wait is None:
            raise TTSError(f"TTS не ответила после {attempt} попыток — {problem}", transient=transient)
        log.warning("tts_retry %d/%d через %.0f с: %s", attempt, len(RETRY_WAITS), wait, problem)
        time.sleep(wait)
    raise AssertionError("unreachable")


def synthesize(text: str, voice: str, instructions: str, s: Settings) -> Path:
    raw = _raw(text, voice, instructions, s)
    if abs(s.tts_speed - 1) <= 0.01:
        return raw
    # ускорение — от закэшированной озвучки: смена tts_speed не заказывает её заново
    path = raw.with_name(f"{raw.stem}_x{s.tts_speed:g}.wav")
    if not path.exists():
        tmp = path.with_suffix(".part.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-filter:a", f"atempo={s.tts_speed}",
                        "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le", str(tmp)], check=True)
        tmp.rename(path)
    return path


def _raw(text: str, voice: str, instructions: str, s: Settings) -> Path:
    key = f"{s.tts_provider}|{s.tts_model}|{voice}|{instructions}|{text}"
    path = s.media_dir / "tts" / f"{hashlib.sha256(key.encode()).hexdigest()[:24]}.wav"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    if s.tts_provider == "dummy":
        write_wav(tmp, babble(text))
        tmp.rename(path)
        return path
    if s.tts_provider == "elevenlabs":
        r = _post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=pcm_{RATE}",
                  headers={"xi-api-key": s.tts_api_key},
                  json={"text": text, "model_id": s.tts_model, "language_code": "ru"})
    else:
        r = _post(f"{s.tts_base_url.rstrip('/')}/audio/speech",
                  headers={"Authorization": f"Bearer {s.tts_api_key}"},
                  json={"model": s.tts_model, "voice": voice, "input": text,
                        "instructions": instructions, "response_format": "pcm"})
    write_wav(tmp, np.frombuffer(r.content[: len(r.content) // 2 * 2], dtype="<i2").astype(np.float32) / 32768)
    tmp.rename(path)
    return path


def babble(text: str) -> np.ndarray:
    """Слоги-бипы с паузами на пробелах: ~4.5 слога в секунду, как живая речь."""
    rng = np.random.default_rng(len(text))
    out = []
    for word in text.split():
        for _ in range(max(1, sum(ch in "аеёиоуыэюяaeiouy" for ch in word.lower()))):
            t = np.arange(int(RATE * 0.18)) / RATE
            env = np.sin(np.pi * t / t[-1]) ** 2
            out.append(0.3 * env * np.sin(2 * np.pi * rng.uniform(140, 220) * t))
        out.append(np.zeros(int(RATE * (0.35 if word[-1] in ".!?" else 0.08))))
    return np.concatenate(out) if out else np.zeros(RATE)


def write_wav(path: Path, samples: np.ndarray, rate: int = RATE) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


def loudnorm(path: Path) -> tuple[np.ndarray, int]:
    """Дорожка, приведённая к одной громкости (EBU R128, -16 LUFS) с поджатым разбросом между фразами.

    Каждый сюжет озвучивается отдельным запросом, и модель отдаёт их то тише, то громче — в выпуске это скачет.
    """
    out = path.with_suffix(".norm.wav")
    if not out.exists():
        tmp = path.with_suffix(".norm.part.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-af", "loudnorm=I=-16:LRA=5:TP=-1.5",
                        "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le", str(tmp)], check=True)
        tmp.rename(out)
    return read_wav(out)


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Моно float32 и частота (файлы пишет только write_wav: 16 бит, моно)."""
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768, w.getframerate()
