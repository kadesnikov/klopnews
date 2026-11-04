"""Погода в начале выпуска: прогноз на день (Open-Meteo, без ключа) и во что одеть Клопика.

Цифры — только отсюда, модель их пересказывает. Наряд выбирается кодом по прогнозу, а не моделью.
"""
import logging

import httpx

log = logging.getLogger(__name__)
API = "https://api.open-meteo.com/v1/forecast"
DAILY = "temperature_2m_max,temperature_2m_min"
# Небо, осадки и ветер — по часам, а не дневным итогам: дневной weather_code — самый плохой час суток, и ночная
# морось в 3 часа надевала Клопику зонтик на весь сухой день; так же и ночной порыв ветра.
HOURLY = "weather_code,precipitation,precipitation_probability,wind_speed_10m"
DAY = range(7, 22)  # часы, когда люди на улице

# коды WMO → небо
SNOW, RAIN, FOG = {71, 73, 75, 77, 85, 86}, {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}, {45, 48}
WORDS = {"sun": "ясно", "cloud": "облачно", "fog": "туман", "rain": "дождь", "snow": "снег", "storm": "гроза"}


def fetch(cfg: dict) -> dict | None:
    """Прогноз на сегодня: {'tmin', 'tmax', 'code', 'rain_prob', 'rain_mm', 'wind'} (wind — м/с)."""
    try:
        r = httpx.get(API, timeout=15, params={"latitude": cfg["lat"], "longitude": cfg["lon"], "daily": DAILY,
                                                "hourly": HOURLY, "timezone": cfg.get("timezone", "Europe/Prague"),
                                                "forecast_days": 1})
        r.raise_for_status()
        return parse(r.json())
    except Exception as e:
        log.warning("weather_failed: %r", e)
        return None


def parse(payload: dict) -> dict | None:
    try:
        d, h = payload["daily"], payload["hourly"]
        day = [i for i, t in enumerate(h["time"]) if int(t[11:13]) in DAY]
        return {"tmin": round(d["temperature_2m_min"][0]), "tmax": round(d["temperature_2m_max"][0]),
                "code": max(int(h["weather_code"][i] or 0) for i in day),
                "rain_prob": max(int(h["precipitation_probability"][i] or 0) for i in day),
                "rain_mm": round(sum(float(h["precipitation"][i] or 0) for i in day), 1),
                "wind": round(max(float(h["wind_speed_10m"][i] or 0) for i in day) / 3.6)}
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def sky(w: dict) -> str:
    code = w["code"]
    if code in SNOW:
        return "snow"
    if code >= 95:
        return "storm"
    if code in RAIN and (w["rain_mm"] >= 1 or w["rain_prob"] >= 50):  # капля мороси при 20% — ещё не дождь
        return "rain"
    if code in FOG:
        return "fog"
    return "sun" if code <= 1 else "cloud"


def gear(w: dict) -> str:
    """Наряд Клопика: зонтик, шапка с варежками, тёмные очки — можно вместе («winter+sunglasses»)."""
    s, out = sky(w), set()
    if s in ("rain", "storm"):
        out.add("umbrella")
    if s == "snow" or w["tmax"] <= 3:
        out.add("winter")
    if s == "sun" and w["tmax"] >= 10:
        out.add("sunglasses")
    return "+".join(sorted(out))


def temp(t: int) -> str:
    return f"{'+' if t > 0 else ('−' if t < 0 else '')}{abs(t)}°"


def summary(w: dict) -> str:
    """Строка для экрана и для модели: «+8…+14°, дождь, 80%, ветер 5 м/с»."""
    s = sky(w)
    rain = f", осадки {w['rain_prob']}%" if w["rain_prob"] >= 30 else ""
    return f"{temp(w['tmin'])}…{temp(w['tmax'])}, {WORDS[s]}{rain}, ветер до {w['wind']} м/с"
