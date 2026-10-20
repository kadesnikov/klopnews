from klopnews.feeds import parse

RSS = """<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
<item><title>Apple &amp; HomeView</title><link>https://ex.com/a</link>
<description>&lt;p&gt;Экран &lt;b&gt;7"&lt;/b&gt;&lt;/p&gt;&lt;img src="https://ex.com/i.jpg"&gt;</description>
<pubDate>Tue, 20 Oct 2026 06:00:00 GMT</pubDate></item>
<item><title>Без ссылки</title></item>
<item><title>С медиа</title><link>https://ex.com/b</link><media:content url="https://ex.com/m.jpg" medium="image"/></item>
</channel></rss>"""


def test_parse_cleans_html_and_finds_images():
    items = parse("src", RSS)
    assert [i["url"] for i in items] == ["https://ex.com/a", "https://ex.com/b"]
    assert items[0]["title"] == "Apple & HomeView"
    assert items[0]["summary"] == 'Экран 7"'
    assert items[0]["image_url"] == "https://ex.com/i.jpg"
    assert items[0]["published_at"].hour == 6
    assert items[1]["image_url"] == "https://ex.com/m.jpg"
