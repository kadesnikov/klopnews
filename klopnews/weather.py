"""Погода в начале выпуска: прогноз на день (Open-Meteo, без ключа) и во что одеть Клопика.

Цифры — только отсюда, модель их пересказывает. Наряд выбирается кодом по прогнозу, а не моделью.
"""
import logging

import httpx

log = logging.getLogger(__name__)
API = "https://api.open-meteo.com/v1/forecast"
DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,wind_speed_10m_max"

# коды WMO → небо
SNOW, RAIN, FOG = {71, 73, 75, 77, 85, 86}, {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}, {45, 48}
WORDS = {"sun": "ясно", "cloud": "облачно", "fog": "туман", "rain": "дождь", "snow": "снег", "storm": "гроза"}


def fetch(cfg: dict) -> dict | None:
    """Прогноз на сегодня: {'tmin', 'tmax', 'code', 'rain_prob', 'rain_mm', 'wind'} (wind — м/с)."""
    try:
        r = httpx.get(API, timeout=15, params={"latitude": cfg["lat"], "longitude": cfg["lon"], "daily": DAILY,
                                                "timezone": cfg.get("timezone", "Europe/Prague"), "forecast_days": 1})
        r.raise_for_status()
        return parse(r.json())
    except Exception as e:
        log.warning("weather_failed: %r", e)
        return None


def parse(payload: dict) -> dict | None:
    try:
        d = payload["daily"]
        return {"tmin": round(d["temperature_2m_min"][0]), "tmax": round(d["temperature_2m_max"][0]),
                "code": int(d["weather_code"][0]), "rain_prob": int(d["precipitation_probability_max"][0] or 0),
                "rain_mm": float(d["precipitation_sum"][0] or 0), "wind": round(d["wind_speed_10m_max"][0] / 3.6)}
    except (KeyError, IndexError, TypeError):
        return None


def sky(w: dict) -> str:
    code = w["code"]
    if code in SNOW:
        return "snow"
    if code >= 95:
        return "storm"
    if code in RAIN or (w["rain_prob"] >= 60 and w["rain_mm"] >= 1):
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
