"""Ночная уборка медиа: без неё озвучка, сюжеты и выпуски копятся на диске вечно.

Сроки — по дате изменения файла:
- clips/<день>/  — промежуточные ролики сюжетов: выпуски из них уже склеены;
- tts/           — кэш озвучки: нужная фраза синтезируется заново;
- episodes/      — готовые выпуски (mp4 + обложка); страница старого выпуска пишет, что он удалён.
"""
import logging
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)

CLIPS_DAYS = 7
TTS_DAYS = 14
EPISODES_DAYS = 90


def _old(path: Path, days: int, now: float) -> bool:
    return now - path.stat().st_mtime > days * 86400


def cleanup_media(media_dir: Path, now: float | None = None) -> int:
    """Удаляет устаревшие файлы, возвращает число освобождённых байт."""
    now = time.time() if now is None else now
    freed = 0
    clips = media_dir / "clips"
    if clips.is_dir():
        for d in clips.iterdir():
            if d.is_dir() and _old(d, CLIPS_DAYS, now):
                freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                shutil.rmtree(d, ignore_errors=True)
    for folder, days, pattern in (("tts", TTS_DAYS, "*.wav"), ("episodes", EPISODES_DAYS, "*.*")):
        root = media_dir / folder
        if not root.is_dir():
            continue
        for f in root.glob(pattern):  # без подпапок: episodes/voices — образцы голосов, их не трогаем
            if f.is_file() and _old(f, days, now):
                freed += f.stat().st_size
                f.unlink(missing_ok=True)
    log.info("media_cleanup freed_mb=%d", freed // 2**20)
    return freed
