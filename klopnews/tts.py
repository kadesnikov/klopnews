"""Озвучка в WAV с кэшем на диске по хешу (модель, голос, инструкция, текст).

openai     — любой OpenAI-совместимый /audio/speech, сырой PCM 24 кГц (gpt-4o-mini-tts понимает instructions)
elevenlabs — ElevenLabs, голос = voice_id; лучший русский, но дороже
dummy      — без ключа: «бу-бу-бу» по слогам, чтобы рот Клопа двигался при локальной разработке

Все модели читают медленнее телеведущих, поэтому дорожка ускоряется на tts_speed без смены тона.
"""
import hashlib
import subprocess
import wave
from pathlib import Path

import httpx
import numpy as np

from klopnews.config import Settings

RATE = 24000  # gpt-4o-mini-tts отдаёт 24 кГц моно


def synthesize(text: str, voice: str, instructions: str, s: Settings) -> Path:
    key = f"{s.tts_provider}|{s.tts_model}|{voice}|{instructions}|{s.tts_speed}|{text}"
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
        r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=pcm_{RATE}", timeout=180,
                       headers={"xi-api-key": s.tts_api_key},
                       json={"text": text, "model_id": s.tts_model, "language_code": "ru"})
    else:
        r = httpx.post(f"{s.tts_base_url.rstrip('/')}/audio/speech", timeout=180,
                       headers={"Authorization": f"Bearer {s.tts_api_key}"},
                       json={"model": s.tts_model, "voice": voice, "input": text,
                             "instructions": instructions, "response_format": "pcm"})
    r.raise_for_status()
    write_wav(tmp, np.frombuffer(r.content[: len(r.content) // 2 * 2], dtype="<i2").astype(np.float32) / 32768)
    if abs(s.tts_speed - 1) > 0.01:
        fast = path.with_suffix(".fast.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp), "-filter:a", f"atempo={s.tts_speed}",
                        "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le", str(fast)], check=True)
        tmp.unlink()
        tmp = fast
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
