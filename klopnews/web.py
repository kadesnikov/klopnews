"""Сайт: страница выпуска (открыта по ссылке — удобно делиться), список выпусков и настройки (по входу из бота)."""
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from klopnews import stats
from klopnews.auth import sign, verify
from klopnews.config import catalog, settings
from klopnews.db import Episode, User, View, session, utcnow
from klopnews.producer import date_label

HERE = Path(__file__).parent
SESSION_TTL = 180 * 24 * 3600
app = FastAPI(title="Клоп·Новости", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
settings().media_dir.joinpath("episodes").mkdir(parents=True, exist_ok=True)
app.mount("/media/episodes", StaticFiles(directory=settings().media_dir / "episodes"), name="episodes")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["mmss"] = lambda t: f"{int(t) // 60}:{int(t) % 60:02d}"
templates.env.globals["date_label"] = date_label
templates.env.filters["pct"] = lambda x: "—" if x is None else f"{round(x * 100)}%"


def current_user(request: Request, db) -> User | None:
    uid = verify(request.cookies.get("session"), settings().session_secret)
    return db.get(User, uid) if uid else None


def is_admin(user: User | None) -> bool:
    return bool(user) and user.tg_id in settings().admin_ids


def page(request: Request, name: str, **ctx) -> HTMLResponse:
    s = settings()
    return templates.TemplateResponse(request, name, {"bot": s.telegram_bot_username, "public_url": s.public_url,
                                                      "topics": catalog()["topics"],
                                                      "admin": is_admin(ctx.get("user")), **ctx})


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow: /\n"  # семейный сайт: выпуски открываются только по ссылке, поисковикам нечего индексировать


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with session() as db:
        user = current_user(request, db)
        episodes = db.scalars(select(Episode).where(Episode.user_id == user.id)
                              .order_by(Episode.created_at.desc()).limit(30)).all() if user else []
        return page(request, "index.html", user=user, episodes=episodes)


@app.get("/e/{slug}", response_class=HTMLResponse)
def episode(request: Request, slug: str):
    with session() as db:
        ep = db.scalar(select(Episode).where(Episode.slug == slug))
        if not ep:
            raise HTTPException(404, "Выпуск не найден")
        owner = db.get(User, ep.user_id)
        return page(request, "episode.html", ep=ep, owner=owner, user=current_user(request, db))


@app.post("/e/{slug}/watch", status_code=204)
async def watch(request: Request, slug: str):
    """Пинг плеера: «досмотрели до t, проиграли p секунд». Строка на каждое открытие страницы."""
    try:
        body = await request.json()
        key, t, played = str(body["k"]), float(body.get("t", 0)), float(body.get("p", 0))
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400)
    if not re.fullmatch(r"[a-z0-9]{8,32}", key):
        raise HTTPException(400)
    with session() as db:
        ep = db.scalar(select(Episode).where(Episode.slug == slug))
        if not ep:
            raise HTTPException(404)
        cap = max(ep.duration, 1.0)
        v = db.scalar(select(View).where(View.key == key))
        if v is None:
            viewer = current_user(request, db)
            # ссылка из уведомления бота помечена src=tg: без cookie это почти наверняка сам адресат
            vid = viewer.id if viewer else (ep.user_id if body.get("src") == "tg" else None)
            v = View(key=key, episode_id=ep.id, viewer_id=vid)
            db.add(v)
        elif v.episode_id != ep.id:
            raise HTTPException(400)
        v.max_t = max(v.max_t or 0.0, min(t, cap))
        v.played = max(v.played or 0.0, min(played, cap * 4))
        v.updated_at = utcnow()
        try:
            db.commit()
        except IntegrityError:  # два первых пинга одновременно — второй не нужен
            db.rollback()


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request, days: int = 14):
    with session() as db:
        user = current_user(request, db)
        if not is_admin(user):
            raise HTTPException(404)
        days = max(1, min(90, days))
        return page(request, "admin.html", user=user, s=stats.overview(db, utcnow(), catalog(), days))


@app.get("/login")
def login(t: str = "", next: str = "/"):
    uid = verify(t, settings().session_secret)
    if not uid:
        return RedirectResponse("/?expired=1", 303)
    resp = RedirectResponse(next if next.startswith("/") and not next.startswith("//") else "/", 303)
    resp.set_cookie("session", sign(uid, SESSION_TTL, settings().session_secret), max_age=SESSION_TTL,
                    httponly=True, samesite="lax", secure=settings().public_url.startswith("https://"))
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/", 303)
    resp.delete_cookie("session")
    return resp


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0):
    with session() as db:
        user = current_user(request, db)
        if not user:
            return RedirectResponse("/", 303)
        return page(request, "settings.html", user=user, sources=catalog()["sources"], saved=saved)


@app.post("/settings")
async def save_settings(request: Request):
    form, cat = await request.form(), catalog()
    with session() as db:  # синхронная SQLite в async-обработчике: на пятерых пользователей это не важно
        user = current_user(request, db)
        if not user:
            return RedirectResponse("/", 303)
        user.name = str(form.get("name", "")).strip()[:80] or user.name
        user.minutes = max(3, min(20, int(form.get("minutes") or user.minutes)))
        t = str(form.get("delivery_time", ""))
        user.delivery_time = t if len(t) == 5 and t[2] == ":" else user.delivery_time
        user.topics = [x for x in form.getlist("topics") if x in cat["topics"]]
        enabled = set(form.getlist("sources"))
        user.muted_sources = [sid for sid in cat["sources"] if sid not in enabled]
        db.commit()
    return RedirectResponse("/settings?saved=1", 303)
