"""Цифры для /admin: кто подключён, открывают ли выпуски, сколько досматривают и где бросают; журнал редакции."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, select

from klopnews.db import Edition, Episode, Selection, Story, User, View

DONE = 0.9  # досмотрел, если проиграл 90% выпуска


def share(v: View, ep: Episode) -> float:
    """Доля выпуска, которую реально проиграли (перемотка вперёд не в счёт)."""
    return min(1.0, v.played / ep.duration) if ep.duration else 0.0


def chapter_at(ep: Episode, t: float) -> dict | None:
    """Глава, на которой человек остановился."""
    cur = None
    for c in ep.chapters:
        if c["t"] <= t:
            cur = c
    return cur


def overview(db, now: datetime, catalog: dict, days: int = 14) -> dict:
    since = now - timedelta(days=days)
    users = db.scalars(select(User).order_by(User.created_at)).all()
    eps = db.scalars(select(Episode).where(Episode.created_at >= since).order_by(Episode.created_at.desc())).all()
    by_ep = defaultdict(list)
    for v in db.scalars(select(View).where(View.episode_id.in_([e.id for e in eps]))).all():
        by_ep[v.episode_id].append(v)
    names = {u.id: u.name for u in users}

    rows, per_user, stops = [], defaultdict(list), Counter()
    for ep in eps:
        own = [v for v in by_ep[ep.id] if v.viewer_id == ep.user_id]
        guests = [v for v in by_ep[ep.id] if v.viewer_id != ep.user_id and v.played >= 5]
        best = max((v for v in own if v.played >= 5), key=lambda v: share(v, ep), default=None)
        sh = share(best, ep) if best else None  # None — не открывал или открыл, но не включал
        if best and sh < DONE:
            c = chapter_at(ep, best.max_t)
            stops[catalog["topics"].get((c or {}).get("topic"), {}).get("title") or (c or {}).get("title") or "Интро"] += 1
        rows.append({"ep": ep, "owner": names.get(ep.user_id, "?"), "opened": bool(own), "share": sh,
                     "guests": len(guests), "last": max((v.updated_at for v in by_ep[ep.id]), default=None)})
        per_user[ep.user_id].append(rows[-1])

    people = []
    for u in users:
        rs = per_user[u.id]
        watched = [r["share"] for r in rs if r["share"] is not None]
        people.append({"user": u, "episodes": len(rs), "opened": sum(r["opened"] for r in rs),
                       "share": sum(watched) / len(watched) if watched else None,
                       "last": max((r["last"] for r in rs if r["last"]), default=None)})

    daily = []
    all_views = [v for vs in by_ep.values() for v in vs]
    for i in range(days - 1, -1, -1):
        day = (now - timedelta(days=i)).date()
        vs = [v for v in all_views if v.started_at.date() == day and v.played >= 5]
        daily.append({"day": day, "views": len(vs), "minutes": round(sum(v.played for v in vs) / 60)})

    delivered = len(rows)
    watched = [r["share"] for r in rows if r["share"] is not None]
    stats_by_ed = dict(db.execute(select(Story.edition, func.count()).where(Story.status == "failed")
                                  .group_by(Story.edition)).all())
    editions = db.scalars(select(Edition).order_by(Edition.date.desc()).limit(7)).all()
    return {
        "days": days,
        "tiles": {"users": len(users), "delivered": delivered,
                  "opened": sum(r["opened"] for r in rows) / delivered if delivered else None,
                  "share": sum(watched) / len(watched) if watched else None,
                  "done": sum(s >= DONE for s in watched),
                  "guests": sum(r["guests"] for r in rows),
                  "hours": round(sum(v.played for v in all_views) / 3600, 1)},
        "people": people, "episodes": rows[:40], "daily": daily,
        "stops": stops.most_common(6),
        "editions": [{"ed": e, "failed": stats_by_ed.get(e.date, 0)} for e in editions],
    }


STORY_STATE = {"ready": "озвучен", "new": "написан, не озвучен", "draft": "не написан: не влез ни в чей выпуск",
               "failed": "сбой при сборке"}


def desk(db, edition: str, catalog: dict) -> dict:
    """Журнал редакции за день: по каждой теме — что модель видела, что выбрала, куда это делось дальше."""
    stories = db.scalars(select(Story).where(Story.edition == edition)).all()
    by_url = {src["url"]: st for st in stories for src in st.sources}
    names = dict(db.execute(select(User.id, User.name)).all())
    aired = defaultdict(list)  # заголовок сюжета → в чьи выпуски попал
    for ep in db.scalars(select(Episode).where(Episode.edition == edition)):
        for c in ep.chapters[1:]:
            aired[c["title"]].append(names.get(ep.user_id, "?"))
    runs = []
    for sel in db.scalars(select(Selection).where(Selection.edition == edition).order_by(Selection.id)):
        events, chosen = [], set()
        for ev in sorted(sel.picked, key=lambda e: -e["importance"]):
            items = [sel.shown[i] for i in ev["items"] if i < len(sel.shown)]
            chosen |= set(ev["items"])
            st = next((by_url[it["url"]] for it in items if it["url"] in by_url), None)
            events.append({"importance": ev["importance"], "reporter_ok": ev["reporter_ok"], "items": items,
                           "story": st, "state": STORY_STATE.get(st.status, st.status) if st else "сюжета нет",
                           "aired": aired.get(st.headline, []) if st else []})
        meta = catalog["topics"].get(sel.topic, {})
        runs.append({"sel": sel, "title": meta.get("title", sel.topic), "color": meta.get("color", "#888"),
                     "events": events, "rest": [it for i, it in enumerate(sel.shown) if i not in chosen],
                     "limit": [it for it in sel.cut if it.get("why") == "limit"],
                     "used": [it for it in sel.cut if it.get("why") == "used"]})
    days = list(db.scalars(select(Selection.edition).distinct().order_by(Selection.edition.desc()).limit(30)))
    return {"edition": edition, "runs": runs, "days": days}
