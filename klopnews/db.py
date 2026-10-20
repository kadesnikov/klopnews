"""SQLite: на семью из пяти человек больше не нужно. Веб и воркер — два процесса, поэтому WAL."""
import secrets
from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(80))
    topics: Mapped[list] = mapped_column(JSON, default=list)  # пусто — все темы
    muted_sources: Mapped[list] = mapped_column(JSON, default=list)
    minutes: Mapped[int] = mapped_column(Integer, default=10)
    delivery_time: Mapped[str] = mapped_column(String(5), default="07:30")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Item(Base):
    """Сырая новость из RSS."""
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String(40), index=True)
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Story(Base):
    """Сюжет выпуска: общий для всех, озвучивается и рендерится один раз."""
    __tablename__ = "stories"
    id: Mapped[int] = mapped_column(primary_key=True)
    edition: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    topic: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(10), default="story")  # story | brief | markets
    importance: Mapped[int] = mapped_column(Integer, default=5)
    headline: Mapped[str] = mapped_column(String(300))
    tag: Mapped[str] = mapped_column(String(40), default="")
    bullets: Mapped[list] = mapped_column(JSON, default=list)
    script: Mapped[str] = mapped_column(Text)
    reporter_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    place: Mapped[str | None] = mapped_column(String(80), nullable=True)
    sources: Mapped[list] = mapped_column(JSON, default=list)  # [{id, name, url}]
    image_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # котировки для блока рынков
    clip_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(10), default="new")  # new | ready | failed


class Edition(Base):
    __tablename__ = "editions"
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), default="building")  # building | ready | failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Episode(Base):
    """Персональный выпуск: склейка готовых клипов под настройки человека."""
    __tablename__ = "episodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    edition: Mapped[str] = mapped_column(String(10))
    slug: Mapped[str] = mapped_column(String(20), unique=True, default=lambda: secrets.token_urlsafe(9))
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    chapters: Mapped[list] = mapped_column(JSON, default=list)  # [{t, title, topic, sources}]
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


@lru_cache
def engine(url: str):
    eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(eng, "connect")
    def _wal(conn, _):
        conn.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(eng)
    return eng


def session(url: str | None = None):
    from klopnews.config import settings

    s = settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return sessionmaker(engine(url or s.db_url), expire_on_commit=False)()
