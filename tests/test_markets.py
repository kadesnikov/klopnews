from klopnews.markets import fmt_change, fmt_price, parse_chart


def test_parse_chart_uses_previous_close():
    payload = {"chart": {"result": [{"meta": {"regularMarketPrice": 110.0, "chartPreviousClose": 90},
                                     "indicators": {"quote": [{"close": [95.0, None, 100.0, 110.0]}]}}]}}
    assert parse_chart(payload) == (110.0, 100.0)
    assert parse_chart({"chart": {"result": None}}) is None


def test_formatting():
    assert fmt_price({"price": 2788.1, "unit": "$"}) == "$2 788,10"
    assert fmt_price({"price": 98123.4}) == "98 123"
    assert fmt_change({"change_pct": -1.27}) == "−1,3%"
    assert fmt_change({"change_pct": 0.42}) == "+0,4%"


def test_bigger_image_url():
    from klopnews.render.studio import bigger_image_url

    assert bigger_image_url("https://ichef.bbci.co.uk/ace/ws/240/cpsprodpb/a/b.jpg") == \
        "https://ichef.bbci.co.uk/ace/ws/1024/cpsprodpb/a/b.jpg"
    assert bigger_image_url("https://x.com/wp/photo-300x200.jpg") == "https://x.com/wp/photo.jpg"
    assert bigger_image_url("https://x.com/a.jpg") == "https://x.com/a.jpg"


def test_holidays():
    from datetime import date

    from klopnews.holidays import easter, is_birthday, orthodox_easter, theme_for

    assert easter(2026) == date(2026, 4, 5) and orthodox_easter(2026) == date(2026, 4, 12)
    assert theme_for(date(2026, 10, 31)).costume == "witch"
    assert theme_for(date(2026, 4, 6)).id == "easter"  # пасхальный понедельник в Чехии
    assert theme_for(date(2026, 5, 9)).costume == ""  # день памяти — без костюмов
    assert theme_for(date(2026, 10, 21)) is None
    assert is_birthday("2000-02-29", date(2027, 2, 28)) and not is_birthday("2000-02-29", date(2028, 2, 28))
    assert not is_birthday(None, date(2026, 1, 1))


def test_weather_gear_and_summary():
    from klopnews import weather

    rain = {"tmin": 7, "tmax": 12, "code": 63, "rain_prob": 85, "rain_mm": 6.0, "wind": 6}
    assert weather.gear(rain) == "umbrella"
    assert weather.summary(rain) == "+7°…+12°, дождь, осадки 85%, ветер до 6 м/с"
    assert weather.gear({**rain, "code": 0, "rain_prob": 0, "rain_mm": 0, "tmax": 24}) == "sunglasses"
    assert weather.gear({**rain, "code": 0, "rain_prob": 0, "rain_mm": 0, "tmin": -8, "tmax": -2}) == "winter"
    assert weather.gear({**rain, "code": 73, "tmin": -6, "tmax": -1}) == "winter"
    assert weather.parse(forecast({})) == {"tmin": 4, "tmax": 10, "code": 3, "rain_prob": 10, "rain_mm": 0.0, "wind": 3}


def forecast(wet: dict) -> dict:
    """Ответ Open-Meteo на 4 ноября: пасмурно и сухо, а в часы wet — {час: (код WMO, мм, вероятность %)}."""
    rows = [wet.get(h, (3, 0.0, 10)) for h in range(24)]
    return {"daily": {"temperature_2m_max": [10.4], "temperature_2m_min": [3.8], "wind_speed_10m_max": [11.0]},
            "hourly": {"time": [f"2026-11-04T{h:02d}:00" for h in range(24)], "weather_code": [r[0] for r in rows],
                       "precipitation": [r[1] for r in rows], "precipitation_probability": [r[2] for r in rows]}}


def test_weather_umbrella_only_for_real_daytime_rain():
    from klopnews import weather

    night = weather.parse(forecast({2: (61, 0.8, 25), 3: (63, 1.6, 25), 4: (51, 0.2, 20)}))  # прошёл до утра
    assert weather.gear(night) == "" and weather.summary(night) == "+4°…+10°, облачно, ветер до 3 м/с"
    drizzle = weather.parse(forecast({14: (51, 0.1, 20)}))  # капля мороси днём при 20% — не дождь
    assert weather.gear(drizzle) == "" and weather.sky(drizzle) == "cloud"
    rain = weather.parse(forecast({9: (61, 0.8, 70), 10: (63, 2.5, 85), 11: (61, 1.0, 80)}))
    assert rain["rain_mm"] == 4.3 and weather.gear(rain) == "umbrella"
    assert weather.summary(rain) == "+4°…+10°, дождь, осадки 85%, ветер до 3 м/с"
    likely = weather.parse(forecast({16: (51, 0.3, 60)}))  # мало, но скорее всего будет
    assert weather.gear(likely) == "umbrella"
    no_ensemble = forecast({10: (63, 3.0, None)})
    no_ensemble["hourly"]["precipitation_probability"] = [None] * 24  # вероятности нет — хватает миллиметров
    assert weather.parse(no_ensemble)["rain_prob"] == 0 and weather.gear(weather.parse(no_ensemble)) == "umbrella"


def test_weekend_markets_without_quotes():
    from klopnews.db import Item
    from klopnews.editor import write_markets

    class LLM:
        def complete(self, system, user):
            self.system, self.user = system, user
            return '{"markets": true, "script": "Биржи отдыхают.", "bullets": ["На неделе — отчёт Nvidia"]}'

    q = [{"name": "S&P 500", "symbol": "^GSPC", "price": 6000.0, "change_pct": 0.5}]
    news = [Item(title="Nvidia отчитается в среду", summary="")]
    llm = LLM()
    sat = write_markets(llm, q, [], news, "2026-11-07")
    assert sat.headline == "Рынки: взгляд на неделю" and sat.data == {} and "S&P" not in llm.user
    assert write_markets(llm, q, [], [], "2026-11-08") is None  # воскресенье без новостей — блока нет
    mon = write_markets(llm, q, [], news, "2026-11-09")
    assert mon.data["quotes"] and "Последние торги были в пятницу" in llm.user
    write_markets(llm, q, [], news, "2026-11-10")
    assert "Последние торги были вчера" in llm.user
