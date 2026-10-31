"""SQLite: на семью из пяти человек больше не нужно. Веб и воркер — два процесса, поэтому WAL."""
import secrets
from datetime import date, datetime, timezone
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
    week_minutes: Mapped[list] = mapped_column(JSON, default=list)  # [пн..вс]; пусто — каждый день minutes
    delivery_time: Mapped[str] = mapped_column(String(5), default="07:30")
    birthday: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD: поздравить в интро
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def minutes_on(self, day: date) -> int:
        """Длина выпуска в этот день недели: в будни можно покороче, в выходные подлиннее."""
        week = self.week_minutes or []
        return week[day.weekday()] if len(week) == 7 else self.minutes


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
    kind: Mapped[str] = mapped_column(String(10), default="story")  # story | brief | markets | events
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


class Event(Base):
    """ИИ-ивент в Праге из афиш и поиска (klopnews/events.py)."""
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True)  # дата:название — склейка дублей
    title: Mapped[str] = mapped_column(String(300))
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # UTC; без времени — полночь по Праге
    has_time: Mapped[bool] = mapped_column(Boolean, default=True)
    place: Mapped[str] = mapped_column(String(300), default="")
    price: Mapped[str] = mapped_column(String(100), default="")
    free: Mapped[bool] = mapped_column(Boolean, default=False)
    lang: Mapped[str] = mapped_column(String(10), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(1000), default="")
    found_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    announced: Mapped[bool] = mapped_column(Boolean, default=False)  # уже был в выпуске


class EventPage(Base):
    """Отпечаток прочитанной страницы-афиши: не изменилась — модель её не перечитывает."""
    __tablename__ = "event_pages"
    url: Mapped[str] = mapped_column(String(1000), primary_key=True)
    text_hash: Mapped[str] = mapped_column(String(64))
    read_at: Mapped[datetime] = mapped_column(DateTime)


class View(Base):
    """Один просмотр страницы выпуска: плеер раз в несколько секунд сообщает, докуда досмотрели."""
    __tablename__ = "views"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True)  # генерирует страница при открытии
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    viewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # None — гость по ссылке
    max_t: Mapped[float] = mapped_column(Float, default=0.0)  # самая дальняя точка, секунды
    played: Mapped[float] = mapped_column(Float, default=0.0)  # сколько секунд реально играло
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


NEW_COLUMNS = [("users", "birthday", "VARCHAR(10)"), ("users", "week_minutes", "JSON")]


@lru_cache
def engine(url: str):
    eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(eng, "connect")
    def _wal(conn, _):
        conn.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(eng)
    with eng.begin() as conn:  # create_all не добавляет колонки в существующие таблицы — дописываем сами
        for table, column, ddl in NEW_COLUMNS:
            if column not in {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    return eng


def session(url: str | None = None):
    from klopnews.config import settings

    s = settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return sessionmaker(engine(url or s.db_url), expire_on_commit=False)()
