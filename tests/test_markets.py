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
