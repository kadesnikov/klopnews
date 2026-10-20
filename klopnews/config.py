"""Настройки из .env. Темы и источники — в config/sources.yaml (их правят чаще кода)."""
import logging
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("/data")
    sources_file: Path = ROOT / "config" / "sources.yaml"
    public_url: str = "http://localhost:8000"
    session_secret: str = "dev-secret-change-me"
    timezone: str = "Europe/Prague"

    # Модель: kimi | anthropic | fake (без ключа — заготовленные ответы для разработки)
    llm_provider: str = "kimi"
    llm_model: str = "kimi-k2.6"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_max_tokens: int = 4000

    # Озвучка: openai (любой OpenAI-совместимый /audio/speech) | elevenlabs | dummy (бип-бип для разработки)
    tts_provider: str = "openai"
    tts_base_url: str = "https://api.openai.com/v1"
    tts_api_key: str = ""
    tts_model: str = "gpt-4o-mini-tts"  # для elevenlabs: eleven_multilingual_v2
    tts_speed: float = 1.15  # ускорение без смены тона (ffmpeg atempo): модели читают медленнее телеведущих
    anchor_voice: str = "ash"  # для elevenlabs — voice_id
    anchor_instructions: str = (
        "Ты — российский телеведущий вечерних новостей, носитель русского языка, родился и вырос в Москве. "
        "Говори по-русски абсолютно без иностранного акцента, с естественными русскими интонациями и ударениями. "
        "Темп живой и бодрый, как в эфире: не растягивай слова, паузы короткие. Тёплый баритон, лёгкая ирония. "
        "Только названия компаний и продуктов латиницей (OpenAI, Apple) произноси как принято в русской речи. "
        "О трагедиях — серьёзно и сдержанно."
    )
    reporter_voice: str = "coral"
    reporter_instructions: str = (
        "Ты — молодая российская телерепортёрша, носитель русского языка из Москвы, ведёшь прямое включение. "
        "Говори по-русски абсолютно без иностранного акцента, с естественными русскими интонациями. "
        "Энергично, быстро, с азартом, но чётко. Названия латиницей произноси как принято в русской речи."
    )
    reporter_enabled: bool = True
    reporters_per_edition: int = 2

    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    allowed_telegram_ids: str = ""  # через запятую; пусто — пускать всех

    edition_time: str = "05:30"  # когда собирать сюжеты дня (по timezone)
    default_delivery_time: str = "07:30"
    default_minutes: int = 10
    fetch_every_minutes: int = 120

    @property
    def allowed_ids(self) -> set[int]:
        return {int(x) for x in self.allowed_telegram_ids.replace(" ", "").split(",") if x}

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'klopnews.db'}"


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx пишет каждый запрос с полным URL, а в URL Telegram API — токен бота
    for name in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


@lru_cache
def settings() -> Settings:
    return Settings()


@lru_cache
def catalog(path: Path | None = None) -> dict:
    """{'topics': {id: {...}}, 'sources': {id: {...}}, 'markets': [...]}"""
    raw = yaml.safe_load((path or settings().sources_file).read_text(encoding="utf-8"))
    return {
        "topics": raw["topics"],
        "sources": {s["id"]: s for s in raw["sources"]},
        "markets": raw.get("markets", []),
    }
