"""Озвучка в WAV с кэшем на диске по хешу (модель, голос, инструкция, текст).

openai — любой OpenAI-совместимый /audio/speech, сырой PCM 24 кГц (gpt-4o-mini-tts понимает instructions: характер голоса)
dummy  — без ключа: «бу-бу-бу» по слогам, чтобы рот Клопа двигался при локальной разработке
"""
import hashlib
import wave
from pathlib import Path

import httpx
import numpy as np

from klopnews.config import Settings

RATE = 24000  # gpt-4o-mini-tts отдаёт 24 кГц моно


def synthesize(text: str, voice: str, instructions: str, s: Settings) -> Path:
    key = f"{s.tts_provider}|{s.tts_model}|{voice}|{instructions}|{text}"
    path = s.media_dir / "tts" / f"{hashlib.sha256(key.encode()).hexdigest()[:24]}.wav"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    if s.tts_provider == "dummy":
        write_wav(tmp, babble(text))
    else:
        r = httpx.post(f"{s.tts_base_url.rstrip('/')}/audio/speech", timeout=180,
                       headers={"Authorization": f"Bearer {s.tts_api_key}"},
                       json={"model": s.tts_model, "voice": voice, "input": text,
                             "instructions": instructions, "response_format": "pcm"})
        r.raise_for_status()
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


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Моно float32 и частота (файлы пишет только write_wav: 16 бит, моно)."""
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768, w.getframerate()
