"""Воркер: раз в минуту смотрит на часы. Сбор RSS, сюжеты дня, выпуски к назначенному времени, бот.

Всё производство идёт под одним замком: /now из бота не столкнётся с утренней сборкой.
"""
import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from klopnews import bot, feeds, producer
from klopnews.config import Settings, catalog, settings, setup_logging
from klopnews.db import Edition, Episode, User, session

log = logging.getLogger(__name__)
LOCK = threading.Lock()
TRIED: set[tuple[int, str]] = set()  # (user, выпуск): не повторять каждую минуту после неудачи


def local_now(s: Settings) -> datetime:
    return datetime.now(ZoneInfo(s.timezone))


def edition_date(s: Settings) -> str:
    return local_now(s).date().isoformat()


def ensure_edition(db, s: Settings) -> str:
    ed = edition_date(s)
    if (row := db.get(Edition, ed)) is None or row.status != "ready":
        producer.build_edition(db, s, ed, local_now(s).astimezone(ZoneInfo("UTC")).replace(tzinfo=None))
    return ed


def deliver(db, s: Settings, user: User, ed: str) -> None:
    ep = producer.assemble(db, s, user, ed)
    if ep is None:
        bot.send(s, user.tg_id, "Сегодня под твои настройки ничего не нашлось 🤷 Попробуй добавить темы или источники.")
        return
    if s.telegram_bot_token:
        bot.notify_episode(s, user, ep, len(ep.chapters) - 1)
    ep.notified = True
    db.commit()


def make_now(user_id: int) -> None:
    s = settings()
    with LOCK, session() as db:
        try:
            deliver(db, s, db.get(User, user_id), ensure_edition(db, s))
        except Exception as e:
            db.rollback()
            log.exception("make_now_failed: %r", e)
            bot.send(s, db.get(User, user_id).tg_id, "Что-то сломалось при сборке выпуска 😿 Уже чиню.")


def tick(s: Settings, last_fetch: float) -> float:
    now = local_now(s)
    hhmm, ed = now.strftime("%H:%M"), now.date().isoformat()
    with LOCK, session() as db:
        if time.time() - last_fetch >= s.fetch_every_minutes * 60:
            feeds.fetch_all(catalog()["sources"])
            last_fetch = time.time()
        if hhmm < s.edition_time:
            return last_fetch
        ensure_edition(db, s)
        done = set(db.scalars(select(Episode.user_id).where(Episode.edition == ed)))
        for user in db.scalars(select(User)).all():
            if user.id not in done and (user.id, ed) not in TRIED and hhmm >= user.delivery_time:
                TRIED.add((user.id, ed))
                try:
                    deliver(db, s, user, ed)
                except Exception as e:
                    db.rollback()
                    log.exception("deliver_failed user=%s: %r", user.id, e)
    return last_fetch


def main() -> None:
    setup_logging()
    s = settings()
    if s.telegram_bot_token:
        threading.Thread(target=bot.poll_forever, args=(s, make_now), daemon=True).start()
    last_fetch = 0.0
    while True:
        try:
            last_fetch = tick(s, last_fetch)
        except Exception as e:
            log.exception("tick_failed: %r", e)
        time.sleep(60)


if __name__ == "__main__":
    main()
