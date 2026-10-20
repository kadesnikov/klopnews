"""Сбор RSS. Каждый фид независим: упал один — остальные собираются."""
import html
import logging
import re
from calendar import timegm
from datetime import datetime, timezone

import feedparser
import httpx
from sqlalchemy import select

from klopnews.db import Item, session, utcnow

log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (compatible; KlopNews/0.1; family news digest)"
_TAG = re.compile(r"<[^>]+>")
_IMG = re.compile(r"<img[^>]+src=[\"']([^\"']+)", re.I)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", text or ""))).strip()


def _image(e) -> str | None:
    for key in ("media_content", "media_thumbnail"):
        for m in e.get(key) or []:
            if m.get("url") and m.get("medium", "image") == "image":
                return m["url"]
    for enc in e.get("enclosures") or []:
        if enc.get("type", "").startswith("image") and enc.get("href"):
            return enc["href"]
    m = _IMG.search(e.get("summary", "") + "".join(c.get("value", "") for c in e.get("content") or []))
    return m.group(1) if m else None


def parse(source_id: str, raw: bytes | str) -> list[dict]:
    feed = feedparser.parse(raw)
    out = []
    for e in feed.entries:
        url, title = e.get("link"), clean(e.get("title", ""))
        if not url or not title:
            continue
        t = e.get("published_parsed") or e.get("updated_parsed")
        out.append({
            "source_id": source_id,
            "url": url,
            "title": title[:500],
            "summary": clean(e.get("summary", ""))[:1500],
            "image_url": _image(e),
            "published_at": datetime.fromtimestamp(timegm(t), timezone.utc).replace(tzinfo=None) if t else utcnow(),
        })
    return out


def fetch_all(sources: dict) -> int:
    """Скачать все фиды и сохранить новые новости. Возвращает число новых."""
    new = 0
    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": UA}) as client, session() as db:
        for sid, src in sources.items():
            try:
                r = client.get(src["url"])
                r.raise_for_status()
                items = parse(sid, r.content)
            except Exception as e:
                log.warning("feed_failed %s: %r", sid, e)
                continue
            known = set(db.scalars(select(Item.url).where(Item.url.in_([i["url"] for i in items]))))
            for i in items:
                if i["url"] not in known:
                    db.add(Item(**i))
                    known.add(i["url"])
                    new += 1
            db.commit()
            log.info("feed %s: %d items", sid, len(items))
    return new


def article(url: str, limit: int = 4000) -> dict:
    """Полный текст статьи и её картинка (og:image), чтобы сюжет был подробнее анонса из RSS."""
    try:
        import trafilatura

        r = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": UA})
        r.raise_for_status()
        meta = trafilatura.extract_metadata(r.text)
        return {"text": (trafilatura.extract(r.text) or "")[:limit], "image": getattr(meta, "image", None)}
    except Exception as e:
        log.info("article_failed %s: %r", url, e)
        return {"text": "", "image": None}
