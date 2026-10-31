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
        if not url or not title or "/videos/" in url:  # ролики (анонсы эфиров) — сюжета из них не сделать
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


# Реклама и дайджесты (пересказ уже вышедших постов вперемешку с мировыми новостями) — мимо
TG_SKIP = re.compile(r"#реклам|#дайджест|\berid\b|^реклама\b", re.I | re.M)
_SEP = re.compile(r"^[_•·—=\-]{3,}$")
_BG = re.compile(r"background-image:\s*url\(['\"]?([^'\")]+)")


def _cls(name: str) -> str:
    return f"contains(concat(' ', normalize-space(@class), ' '), ' {name} ')"


def _more_link(div) -> str | None:
    """Ссылка «Подробности: …» на полную статью на сайте канала (у The Česko — the-cesko.cz)."""
    for a in div.iter("a"):
        prev = a.getprevious()
        before = (prev.tail if prev is not None else a.getparent().text) or ""
        href = a.get("href", "")
        if "одробн" in before[-40:] and href.startswith("http") and "//t.me/" not in href:
            return href
    return None


def parse_telegram(source_id: str, raw: bytes | str) -> list[dict]:
    """Публичный канал Telegram: веб-версия t.me/s/<канал> — последние ~20 постов без ключей и бота.
    Заголовок — первая строка поста, анонс — весь текст без подписи канала. Если в посте есть
    «Подробности: <ссылка>», новость ведёт на статью (за полным текстом), иначе — на сам пост."""
    import lxml.html

    doc = lxml.html.fromstring(raw)
    out = []
    for msg in doc.xpath(f"//div[{_cls('tgme_widget_message')} and @data-post]"):
        texts = msg.xpath(f".//div[{_cls('js-message_text')}]")  # не цитата из ответа (js-message_reply_text)
        if not texts:
            continue
        for br in texts[0].iter("br"):
            br.tail = "\n" + (br.tail or "")
        full = [ln for ln in (re.sub(r"[ \t\xa0\u2800]+", " ", x).strip() for x in texts[0].text_content().split("\n")) if ln]
        if TG_SKIP.search("\n".join(full)):  # пометка «Реклама в Pozor!®» стоит в подписи — смотрим весь пост
            continue
        cut = next((k for k, ln in enumerate(full) if _SEP.match(ln)), len(full))
        lines = full[:cut]  # дальше подпись канала: «___ Pozor!® - новости…», «••• @the_cesko — …»
        text = "\n".join(lines)
        if not text:
            continue
        while lines and re.fullmatch(r"(#\w+\s*)+", lines[0]):  # строка из одних хэштегов — не заголовок
            lines.pop(0)
        first = re.sub(r"^[^\w«\"(]+", "", lines[0]).strip() if lines else ""  # без эмодзи в начале
        title = first if len(first) <= 200 else first[:200].rsplit(" ", 1)[0] + "…"
        if not title:
            continue
        image = None
        for el in msg.xpath(f".//a[{_cls('tgme_widget_message_photo_wrap')}] | .//i[{_cls('tgme_widget_message_video_thumb')}]"):
            if m := _BG.search(el.get("style", "")):
                image = m.group(1)
                break
        when = msg.xpath(f".//a[{_cls('tgme_widget_message_date')}]/time/@datetime")
        published = datetime.fromisoformat(when[0]).astimezone(timezone.utc).replace(tzinfo=None) if when else utcnow()
        out.append({
            "source_id": source_id,
            "url": _more_link(texts[0]) or f"https://t.me/{msg.get('data-post')}",
            "title": title[:500],
            "summary": text[:3000],
            "image_url": image,
            "published_at": published,
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
                items = (parse_telegram if "://t.me/s/" in src["url"] else parse)(sid, r.content)
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
    if url.startswith("https://t.me/"):  # пост из канала: весь текст уже в анонсе
        return {"text": "", "image": None}
    try:
        import trafilatura

        r = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": UA})
        r.raise_for_status()
        meta = trafilatura.extract_metadata(r.text)
        return {"text": (trafilatura.extract(r.text) or "")[:limit], "image": getattr(meta, "image", None)}
    except Exception as e:
        log.info("article_failed %s: %r", url, e)
        return {"text": "", "image": None}
