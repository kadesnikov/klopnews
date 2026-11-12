"""Telegram: вход на сайт и уведомление «выпуск готов». Long polling — вебхук и домен для бота не нужны."""
import logging
import threading
import time
from html import escape

import httpx
from sqlalchemy import select

from klopnews.auth import sign
from klopnews.config import Settings, catalog
from klopnews.db import User, session

log = logging.getLogger(__name__)
LOGIN_TTL = 7 * 24 * 3600
NOW_COOLDOWN = 30 * 60  # /now — не чаще раза в полчаса: каждый выпуск стоит денег (модель и озвучка)
_last_now: dict[int, float] = {}


def api(s: Settings, method: str, **params) -> dict:
    r = httpx.post(f"https://api.telegram.org/bot{s.telegram_bot_token}/{method}", json=params, timeout=70)
    return r.json()


def send(s: Settings, chat_id: int, text: str, button: tuple[str, str] | None = None) -> None:
    if not s.telegram_bot_token:
        log.info("telegram off, message to %s: %s %s", chat_id, text, button or "")
        return
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if button and button[1].startswith("https://"):  # Telegram принимает в кнопках только https
        params["reply_markup"] = {"inline_keyboard": [[{"text": button[0], "url": button[1]}]]}
    elif button:
        params["text"] += f"\n{button[1]}"
    res = api(s, "sendMessage", **params)
    if not res.get("ok"):
        log.warning("send_failed chat=%s: %s", chat_id, res)


def login_url(s: Settings, user: User, next_path: str = "/") -> str:
    return f"{s.public_url}/login?t={sign(user.id, LOGIN_TTL, s.session_secret)}&next={next_path}"


SELF_TITLED = {"weather", "markets"}  # заголовок рубрики уже называет тему: «Погода: …», «Рынки за день»


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def digest(chapters: list[dict], topics: dict, limit: int = 3500) -> str:
    """Текстовая версия выпуска для тех, кому сейчас не до видео: темы, заголовки, пункты с экрана.

    Свёрнутая цитата показывает первые строки — поэтому первым идёт сюжет, а не служебная шапка."""
    stories = chapters[1:]
    groups: list[list[dict]] = []
    for c in stories:
        if groups and groups[-1][0].get("topic") == c.get("topic"):
            groups[-1].append(c)
        else:
            groups.append([c])
    blocks, used, shown = [], 0, 0
    for group in groups:
        title = topics.get(group[0].get("topic"), {}).get("title")
        head = [f"<b>{escape(title)}</b>"] if title and not all(c.get("kind") in SELF_TITLED for c in group) else []
        gap = "\n\n"  # между темами — пустая строка, внутри темы — нет
        for c in group:
            item = f"• {escape(c['title'])}"
            if bullets := c.get("bullets"):
                item += f"\n<i>{escape(' · '.join(bullets))}</i>"
            block = "\n".join([*head, item])
            # у Telegram предел 4096 знаков вместе с тегами; сюжет не рвём — либо целиком, либо «и ещё N»
            if used + len(block) > limit:
                rest = len(stories) - shown
                blocks.append(f"\n\n…и ещё {rest} {plural(rest, 'сюжет', 'сюжета', 'сюжетов')} — в выпуске")
                return f"<blockquote expandable>{''.join(blocks).strip()}</blockquote>"
            blocks.append(gap + block)
            used, shown, head, gap = used + len(block) + 2, shown + 1, [], "\n"
    return f"<blockquote expandable>{''.join(blocks).strip()}</blockquote>" if blocks else ""


def notify_episode(s: Settings, user: User, ep, n_stories: int) -> None:
    minutes = max(1, round(ep.duration / 60))
    text = (f"🐞 <b>Клоп·Новости готовы</b>\n"
            f"{n_stories} {plural(n_stories, 'сюжет', 'сюжета', 'сюжетов')} · {minutes} мин")
    if brief := digest(ep.chapters, catalog()["topics"]):
        text += f"\n\n{brief}"
    send(s, user.tg_id, text, ("▶️ Смотреть выпуск", f"{s.public_url}/e/{ep.slug}?src=tg"))


def handle(s: Settings, msg: dict, make_now) -> None:
    chat, text = msg["chat"]["id"], (msg.get("text") or "").strip()
    frm = msg.get("from") or {}
    if s.allowed_ids and frm.get("id") not in s.allowed_ids:
        send(s, chat, f"Это семейный проект 🐞 Попроси админа добавить твой Telegram ID: <code>{frm.get('id')}</code>")
        return
    with session() as db:
        user = db.scalar(select(User).where(User.tg_id == frm["id"]))
        if not user:
            user = User(tg_id=frm["id"], name=(frm.get("first_name") or "друг")[:80],
                        minutes=s.default_minutes, delivery_time=s.default_delivery_time)
            db.add(user)
            db.commit()
        if text.startswith("/now"):
            wait = _last_now.get(user.id, 0) + NOW_COOLDOWN - time.time()
            if wait > 0 and user.tg_id not in s.admin_ids:
                send(s, chat, f"Выпуск уже собирается или только что собран 🐞 Следующий — через {int(wait // 60) + 1} мин.")
                return
            _last_now[user.id] = time.time()
            send(s, chat, "Собираю выпуск под твои настройки, это пара минут… 🐞")
            threading.Thread(target=make_now, args=(user.id,), daemon=True).start()
        elif text.startswith("/admin") and user.tg_id in s.admin_ids:
            send(s, chat, "Кто подключён, кто смотрит и сколько досматривает:",
                 ("📊 Открыть админку", login_url(s, user, "/admin")))
        elif text.split()[:2] == ["/start", "login"]:  # кнопка «Получить новую ссылку» на сайте, когда старая устарела
            send(s, chat, "Вот свежая ссылка для входа — она работает неделю 🐞", ("🔑 Войти на сайт", login_url(s, user)))
        elif text.startswith("/settings"):
            send(s, chat, "Темы, источники и длительность выпуска:", ("⚙️ Настройки", login_url(s, user, "/settings")))
        else:
            send(s, chat, f"Привет, {user.name}! Я Клоп Александрович, ведущий «Клоп·Новостей» 🐞\n\n"
                          f"Каждый день в {user.delivery_time} пришлю ссылку на твой выпуск. "
                          "Выбери темы и источники — и выпуск будет только про то, что тебе интересно.\n\n"
                          "/now — собрать выпуск прямо сейчас\n/settings — настройки",
                 ("⚙️ Открыть настройки", login_url(s, user, "/settings")))


NAME = "Клоп·Новости"
SHORT = "Твой видеовыпуск новостей каждое утро: ведущий Клоп Александрович и корреспондент Клопик 🐞"
DESCRIPTION = (
    "🐞 Клоп·Новости — семейный видеовыпуск новостей.\n\n"
    "Каждое утро ведущий Клоп Александрович рассказывает главное за сутки, а корреспондент Клопик "
    "выходит в прямое включение с места событий.\n\n"
    "• Только твои темы: ИИ и технологии, мир, Чехия и Прага, наука и космос, рынки\n"
    "• Свои источники и длительность — от 5 до 20 минут\n"
    "• Под каждым сюжетом ссылка на оригинал\n\n"
    "Нажми «Старт», чтобы получить первый выпуск.\n\n"
    "Made by kadesnikov.com"
)
COMMANDS = [{"command": "now", "description": "Собрать выпуск прямо сейчас"},
            {"command": "settings", "description": "Темы, источники и время выпуска"},
            {"command": "start", "description": "Что умеет Клоп"}]


def apply_branding(s: Settings) -> None:
    """Имя, описание и меню команд бот выставляет себе сам — в BotFather руками остаются только картинки."""
    for method, params in [("setMyName", {"name": NAME}), ("setMyShortDescription", {"short_description": SHORT}),
                           ("setMyDescription", {"description": DESCRIPTION}), ("setMyCommands", {"commands": COMMANDS})]:
        try:
            res = api(s, method, **params)
            if not res.get("ok"):
                log.warning("branding %s: %s", method, res.get("description"))
        except Exception as e:
            log.warning("branding %s failed: %r", method, e)


def poll_forever(s: Settings, make_now) -> None:
    apply_branding(s)
    offset = 0
    while True:
        try:
            res = api(s, "getUpdates", offset=offset, timeout=50, allowed_updates=["message"])
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                if "message" in upd:
                    handle(s, upd["message"], make_now)
        except Exception as e:
            log.warning("poll_failed: %r", e)
            threading.Event().wait(5)
