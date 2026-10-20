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
