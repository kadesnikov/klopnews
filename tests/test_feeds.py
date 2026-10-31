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


TG = """<html><body><section class="tgme_channel_history js-message_history">
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="the_cesko/101">
 <div class="tgme_widget_message_bubble">
  <a class="tgme_widget_message_photo_wrap 5 js-message_photo" href="https://t.me/the_cesko/101" style="width:800px;background-image:url('https://cdn4.telesco.pe/file/a.jpg')"></a>
  <div class="tgme_widget_message_text js-message_text" dir="auto"><i class="emoji"><b>🇨🇿</b></i> <b>В Праге подорожает проезд</b><br/><br/>С 1 января билет на 30 минут будет стоить 39 крон.<br/>→ Подробности: <a href="https://the-cesko.cz/life/abc" target="_blank">the-cesko.cz/life/abc</a><br/>•••<br/><a href="https://t.me/the_cesko">@the_cesko</a> — о жизни в Чехии<br/>⠀</div>
  <div class="tgme_widget_message_footer"><a class="tgme_widget_message_date" href="https://t.me/the_cesko/101"><time datetime="2026-10-30T18:01:12+00:00" class="time">19:01</time></a></div>
 </div></div></div>
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="the_cesko/102">
  <div class="tgme_widget_message_reply"><div class="tgme_widget_message_text js-message_reply_text">Цитата чужого поста</div></div>
  <div class="tgme_widget_message_text js-message_text">Лучший обменник в центре!<br/>___<br/>Реклама в Pozor!® 🐊</div>
</div></div>
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="the_cesko/105">
  <div class="tgme_widget_message_text js-message_text">#дайджест<br/>Всё, что было сегодня</div>
</div></div>
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="the_cesko/103">
  <i class="tgme_widget_message_video_thumb" style="background-image:url('https://cdn4.telesco.pe/file/v.jpg')"></i>
</div></div>
<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="the_cesko/104">
  <div class="tgme_widget_message_reply"><div class="tgme_widget_message_text js-message_reply_text">Цитата</div></div>
  <div class="tgme_widget_message_text js-message_text">#Брно<br/>Ответ: в Брно открыли новую линию трамвая<br/>___<br/>Pozor!® - новости и жизнь в Чехии 🐊</div>
  <i class="tgme_widget_message_video_thumb" style="background-image:url('https://cdn4.telesco.pe/file/v2.jpg')"></i>
</div></div>
</section></body></html>"""


def test_parse_telegram_channel_page():
    from klopnews.feeds import parse_telegram

    items = parse_telegram("thecesko", TG)
    # реклама, дайджест и пост без текста — мимо; «Подробности: …» ведут на статью за полным текстом
    assert [i["url"] for i in items] == ["https://the-cesko.cz/life/abc", "https://t.me/the_cesko/104"]
    a, b = items
    assert a["title"] == "В Праге подорожает проезд"
    assert a["summary"].endswith("Подробности: the-cesko.cz/life/abc")  # подпись канала отрезана
    assert a["image_url"] == "https://cdn4.telesco.pe/file/a.jpg"
    assert a["published_at"].hour == 18 and a["published_at"].tzinfo is None
    assert b["title"] == "Ответ: в Брно открыли новую линию трамвая"  # не хэштег и не цитата из ответа
    assert "Pozor" not in b["summary"]
    assert b["image_url"] == "https://cdn4.telesco.pe/file/v2.jpg"
