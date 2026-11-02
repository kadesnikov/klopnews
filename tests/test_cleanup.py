import os
import time

from klopnews.cleanup import cleanup_media


def _file(path, age_days, now):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 10)
    t = now - age_days * 86400
    os.utime(path, (t, t))
    return path


def test_cleanup_keeps_fresh_and_drops_old(tmp_path):
    now = time.time()
    old_clip = _file(tmp_path / "clips" / "2026-01-01" / "a.mp4", 10, now)
    os.utime(old_clip.parent, (now - 10 * 86400,) * 2)
    new_clip = _file(tmp_path / "clips" / "2026-11-01" / "b.mp4", 1, now)
    old_tts, new_tts = _file(tmp_path / "tts" / "a.wav", 20, now), _file(tmp_path / "tts" / "b.wav", 3, now)
    old_ep, new_ep = _file(tmp_path / "episodes" / "old.mp4", 100, now), _file(tmp_path / "episodes" / "new.mp4", 30, now)
    voice = _file(tmp_path / "episodes" / "voices" / "v.mp3", 365, now)

    assert cleanup_media(tmp_path, now) == 30
    assert not old_clip.parent.exists() and not old_tts.exists() and not old_ep.exists()
    assert new_clip.exists() and new_tts.exists() and new_ep.exists() and voice.exists()


def test_cleanup_without_media_dir(tmp_path):
    assert cleanup_media(tmp_path / "nope") == 0
