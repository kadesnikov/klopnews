"""Котировки для блока «Рынки». Цифры только отсюда — модели нельзя придумывать числа."""
import logging

import httpx

log = logging.getLogger(__name__)
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"


def parse_chart(payload: dict) -> tuple[float, float] | None:
    """(последняя цена, предыдущее закрытие) из ответа Yahoo chart API."""
    try:
        res = payload["chart"]["result"][0]
        closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
        price = res["meta"].get("regularMarketPrice") or closes[-1]
        prev = closes[-2] if len(closes) >= 2 else res["meta"]["chartPreviousClose"]
        return float(price), float(prev)
    except (KeyError, IndexError, TypeError):
        return None


def fetch_quotes(instruments: list[dict]) -> list[dict]:
    out = []
    with httpx.Client(timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for ins in instruments:
            try:
                parsed = parse_chart(client.get(CHART.format(symbol=ins["symbol"])).json())
            except Exception as e:
                log.warning("quote_failed %s: %r", ins["symbol"], e)
                parsed = None
            if parsed:
                price, prev = parsed
                out.append({**ins, "price": price, "change_pct": (price / prev - 1) * 100 if prev else 0.0})
    return out


def top_movers(quotes: list[dict], n: int = 6) -> list[dict]:
    """Самые сильные движения дня по модулю."""
    return sorted(quotes, key=lambda q: -abs(q["change_pct"]))[:n]


def fmt_price(q: dict) -> str:
    digits = q.get("digits", 2 if q["price"] < 10_000 else 0)
    s = f"{q['price']:,.{digits}f}".replace(",", " ").replace(".", ",")
    return f"{q.get('unit', '')}{s}"


def fmt_change(q: dict) -> str:
    return f"{q['change_pct']:+.1f}%".replace(".", ",").replace("-", "−")
