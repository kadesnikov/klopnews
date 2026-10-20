"""Ручное управление: python -m klopnews.cli fetch | edition | episode <tg_id> | demo

demo — без сети и ключей: подкладывает тестовые новости, чтобы прогнать весь конвейер
(вместе с LLM_PROVIDER=fake и TTS_PROVIDER=dummy).
"""
import logging
import sys
from datetime import timedelta

from sqlalchemy import select

from klopnews import feeds, producer
from klopnews.config import catalog, settings
from klopnews.db import Item, User, session, utcnow
from klopnews.worker import edition_date, local_now

DEMO = [
    ("verge", "OpenAI показала новую модель с голосовым интерфейсом"),
    ("habr", "В Праге открыли центр разработки ИИ"),
    ("bbcru", "Саммит ЕС обсудил энергетику и бюджет"),
    ("irozhlas", "Пражский метро продлит линию D до Писнице"),
    ("nplus1", "Телескоп James Webb нашёл воду у далёкой экзопланеты"),
    ("cnbc", "Золото обновило рекорд на фоне ожиданий снижения ставки"),
]


def main(argv: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s, cmd = settings(), argv[0] if argv else ""
    with session() as db:
        if cmd == "fetch":
            print("new items:", feeds.fetch_all(catalog()["sources"]))
        elif cmd == "edition":
            producer.build_edition(db, s, edition_date(s), local_now(s).replace(tzinfo=None))
        elif cmd == "episode":
            user = db.scalar(select(User).where(User.tg_id == int(argv[1])))
            ep = producer.assemble(db, s, user, edition_date(s))
            print(f"{s.public_url}/e/{ep.slug}" if ep else "nothing to show")
        elif cmd == "demo":
            for i, (src, title) in enumerate(DEMO):
                url = f"https://example.com/demo/{edition_date(s)}/{i}"
                if not db.scalar(select(Item.id).where(Item.url == url)):
                    db.add(Item(source_id=src, url=url, title=title, summary=title + ". Подробности в статье.",
                                published_at=utcnow() - timedelta(hours=i)))
            if not db.scalar(select(User).where(User.tg_id == 1)):
                db.add(User(tg_id=1, name="Маша", minutes=s.default_minutes, delivery_time=s.default_delivery_time))
            db.commit()
            print("demo items and user tg_id=1 added")
        else:
            print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
