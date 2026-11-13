"""Воркер: раз в минуту смотрит на часы. Сбор RSS, сюжеты дня, выпуски к назначенному времени, бот.

Всё производство идёт под одним замком: /now из бота не столкнётся с утренней сборкой.
"""
import logging
import threading
import time
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy import select

from klopnews import bot, cleanup, feeds, llm, producer
from klopnews.config import Settings, catalog, settings, setup_logging
from klopnews.db import Edition, Episode, User, session

log = logging.getLogger(__name__)
LOCK = threading.Lock()
# (user, выпуск) → (сколько раз не вышло, когда пробовать снова): не долбить каждую минуту, но и не сдаваться сразу
TRIED: dict[tuple[int, str], tuple[int, float]] = {}
RETRY_MINUTES = [10, 20, 40]  # временный сбой (лимит озвучки, сеть) обычно проходит за минуты
ALERTED: set[str] = set()  # о каждой беде админу — один раз, а не каждую минуту
STUCK_AFTER = 45 * 60  # обычная сборка — минут 10–20; дольше часа без движения — воркер завис
_busy_since: float | None = None  # когда начался текущий tick (None — воркер спит между тиками)


def alert(s: Settings, key: str, text: str) -> None:
    """Сломалось то, что без человека не починится: пишем админам в Telegram. Сам алерт не должен ронять воркер."""
    if key in ALERTED:
        return
    ALERTED.add(key)
    log.error("alert %s: %s", key, text)
    for admin in s.admin_ids:
        try:
            bot.send(s, admin, f"🚨 <b>Клоп·Новости: сбой</b>\n{escape(text)}")
        except Exception as e:
            log.warning("alert_send_failed: %r", e)


def watchdog(s: Settings) -> None:
    """Зависший tick держит замок и ничего не логирует — замечаем его из соседнего потока."""
    while True:
        time.sleep(60)
        since = _busy_since
        if since is not None and time.time() - since > STUCK_AFTER:
            alert(s, f"stuck:{since}", f"Воркер завис: сборка идёт уже {int((time.time() - since) // 60)} мин. "
                                        "Нужен docker compose restart worker.")


def local_now(s: Settings) -> datetime:
    return datetime.now(ZoneInfo(s.timezone))


def edition_date(s: Settings) -> str:
    return local_now(s).date().isoformat()


def ensure_edition(db, s: Settings) -> str:
    ed = edition_date(s)
    if (row := db.get(Edition, ed)) is not None and row.status == "ready":
        return ed
    with producer.build_lock(s):  # пока cli что-то пересобирает — ждём его, а не дублируем
        db.expire_all()
        if (row := db.get(Edition, ed)) is None or row.status != "ready":
            producer.build_edition(db, s, ed, local_now(s).astimezone(ZoneInfo("UTC")).replace(tzinfo=None))
    return ed


def deliver(db, s: Settings, user: User, ed: str) -> None:
    llm.problem = None
    ep = producer.assemble(db, s, user, ed)
    if ep is None and llm.problem:  # пусто не потому, что новостей нет, а потому что модели нечем платить
        raise llm.LLMBillingError(llm.problem)
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
            alert(s, f"now:{edition_date(s)}:{e!r}"[:200], f"/now не собрал выпуск: {e!r}")
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
            fails, next_try = TRIED.get((user.id, ed), (0, 0.0))
            if user.id in done or hhmm < user.delivery_time or time.time() < next_try or fails > len(RETRY_MINUTES):
                continue
            try:
                deliver(db, s, user, ed)
            except Exception as e:
                db.rollback()
                log.exception("deliver_failed user=%s attempt=%d: %r", user.id, fails + 1, e)
                if fails < len(RETRY_MINUTES):
                    TRIED[(user.id, ed)] = (fails + 1, time.time() + RETRY_MINUTES[fails] * 60)
                    alert(s, f"deliver:{user.id}:{ed}", f"Не доставлен выпуск для {user.name}: {e!r}. "
                                                        f"Повторю через {RETRY_MINUTES[fails]} мин.")
                else:
                    TRIED[(user.id, ed)] = (fails + 1, float("inf"))
                    alert(s, f"deliver_gave_up:{user.id}:{ed}", f"Сдался: выпуск для {user.name} так и не собрался "
                                                                f"после {fails + 1} попыток. Последняя ошибка: {e!r}")
    return last_fetch


def safe_tick(s: Settings, last_fetch: float) -> float:
    """Один оборот воркера: падение не останавливает цикл, а сторож видит, сколько он длится."""
    global _busy_since
    _busy_since = time.time()
    try:
        return tick(s, last_fetch)
    except Exception as e:
        log.exception("tick_failed: %r", e)
        alert(s, f"tick:{edition_date(s)}", f"Выпуск на {edition_date(s)} не собирается: {e!r}. "
                                           "Воркер повторяет попытку каждую минуту, подробности — в логах.")
        return last_fetch
    finally:
        _busy_since = None


def main() -> None:
    setup_logging()
    s = settings()
    if problems := s.production_problems():
        raise SystemExit("unsafe production config: " + "; ".join(problems))
    if not s.public_url.startswith("https://"):
        log.warning("PUBLIC_URL=%s — ссылки в Telegram будут без кнопки и не откроются снаружи", s.public_url)
    if s.telegram_bot_token:
        threading.Thread(target=bot.poll_forever, args=(s, make_now), daemon=True).start()
    threading.Thread(target=watchdog, args=(s,), daemon=True).start()
    last_fetch, cleaned = 0.0, ""
    while True:
        last_fetch = safe_tick(s, last_fetch)
        if (today := local_now(s).date().isoformat()) != cleaned:
            cleaned = today
            try:
                cleanup.cleanup_media(s.media_dir)
            except Exception as e:
                log.exception("cleanup_failed: %r", e)
        time.sleep(60)


if __name__ == "__main__":
    main()
