"""Ручное управление: python -m klopnews.cli fetch | edition | rerender | episode <tg_id> | voices [голос ...] | demo

rerender — заново озвучить и отрендерить клипы сегодняшних сюжетов (после смены голоса или дизайна).

voices — один и тот же текст разными голосами, чтобы выбрать на слух (ссылки на mp3 печатаются).

demo — без сети и ключей: подкладывает тестовые новости, чтобы прогнать весь конвейер
(вместе с LLM_PROVIDER=fake и TTS_PROVIDER=dummy).
"""
import sys
from datetime import timedelta

from sqlalchemy import select

from klopnews import feeds, producer
from klopnews.config import catalog, settings, setup_logging
from klopnews.db import Item, Story, User, session, utcnow
from klopnews.render import studio
from klopnews.tts import synthesize
from klopnews.worker import edition_date

VOICES = ["ash", "onyx", "echo", "verse", "ballad", "cedar", "coral", "nova", "sage", "shimmer", "marin"]
SAMPLE = ("Добрый вечер! В эфире Клоп Новости, с вами Клоп Александрович. Компания OpenAI представила новую модель, "
          "а Apple снизила цены на iPhone. Курс евро к кроне — двадцать четыре и три десятых. "
          "В Праге открыли новую станцию метро. Одобряю!")

DEMO = [
    ("verge", "OpenAI показала новую модель с голосовым интерфейсом"),
    ("habr", "В Праге открыли центр разработки ИИ"),
    ("bbcru", "Саммит ЕС обсудил энергетику и бюджет"),
    ("irozhlas", "Пражский метро продлит линию D до Писнице"),
    ("nplus1", "Телескоп James Webb нашёл воду у далёкой экзопланеты"),
    ("marketwatch", "Золото обновило рекорд на фоне ожиданий снижения ставки"),
]


def main(argv: list[str]) -> None:
    setup_logging()
    s, cmd = settings(), argv[0] if argv else ""
    with session() as db:
        if cmd == "fetch":
            print("new items:", feeds.fetch_all(catalog()["sources"]))
        elif cmd == "edition":
            with producer.build_lock(s):
                producer.build_edition(db, s, edition_date(s), utcnow())
        elif cmd == "rerender":
            ed = edition_date(s)
            with producer.build_lock(s):
                for st in db.scalars(select(Story).where(Story.edition == ed)).all():
                    st.status = "new"
                db.commit()
                for old in (s.media_dir / "clips" / ed).glob("outro*.mp4"):  # финал общий на день — тоже заново
                    old.unlink()
                producer.build_edition(db, s, ed, utcnow(), write=False)
        elif cmd == "episode":
            user = db.scalar(select(User).where(User.tg_id == int(argv[1])))
            ep = producer.assemble(db, s, user, edition_date(s))
            print(f"{s.public_url}/e/{ep.slug}" if ep else "nothing to show")
        elif cmd == "voices":
            out = s.media_dir / "episodes" / "voices"
            out.mkdir(parents=True, exist_ok=True)
            for voice in argv[1:] or VOICES:
                try:
                    wav = synthesize(SAMPLE, voice, s.anchor_instructions, s)
                    mp3 = out / f"{s.tts_provider}-{voice}-{s.tts_speed}.mp3"
                    studio.ffmpeg(["-i", str(wav), "-b:a", "96k", str(mp3)])
                    print(f"{voice:>10}: {s.public_url}/media/episodes/voices/{mp3.name}")
                except Exception as e:
                    print(f"{voice:>10}: ошибка {e!r}")
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
