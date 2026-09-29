from datetime import datetime, timezone
from pathlib import Path

from ingest import links

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc)
JSONLD_PAGE = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"MusicEvent","name":"Late Show",
 "startDate":"2026-10-20T21:00:00","url":"https://venue.example.com/late-show",
 "location":{"@type":"Place","name":"The Chapel","address":"777 Valencia St, San Francisco, CA"}}
</script></head><body>x</body></html>"""


def _no_llm(text, context):
    raise AssertionError("LLM should not be called")


def test_partiful_link_uses_parser_and_allows_private_events():
    html = (FIX / "partiful_event.html").read_text().replace('"isPublic": true', '"isPublic": false')
    r = links.resolve("https://partiful.com/e/79v5MvdmXJreSkcvfI4n?source=share",
                      fetch=lambda u: html, extract_text=_no_llm, now=NOW)
    assert r.error is None and r.events[0].title == "Come read with us"


def test_json_ld_page_naive_time_is_pacific():
    r = links.resolve("https://venue.example.com/late-show", fetch=lambda u: JSONLD_PAGE,
                      extract_text=_no_llm, now=NOW)
    e = r.events[0]
    assert e.start_time == datetime(2026, 10, 21, 4, 0, tzinfo=timezone.utc)  # 9pm PDT
    assert e.location == "The Chapel, 777 Valencia St, San Francisco, CA"
    assert e.url == "https://venue.example.com/late-show"


def test_page_without_structured_data_goes_to_llm_with_page_text():
    seen = {}

    def llm(text, context):
        seen.update(text=text, context=context)
        return [{"title": "Found"}]

    html = "<html><body><h1>Poetry Night</h1><p>Oct 3, 7pm</p><script>x()</script></body></html>"
    r = links.resolve("https://blog.example.com/p", fetch=lambda u: html, extract_text=llm, now=NOW)
    assert r.candidates == [{"title": "Found", "url": "https://blog.example.com/p"}]  # page URL inherited
    assert "Poetry Night" in seen["text"] and "x()" not in seen["text"]
    assert "https://blog.example.com/p" in seen["context"]


def test_fetch_failure_and_empty_page():
    def boom(u):
        raise RuntimeError("403")

    assert links.resolve("https://x.example.com", fetch=boom, extract_text=_no_llm,
                         now=NOW).error == "couldn't read this page"
    empty = links.resolve("https://x.example.com", fetch=lambda u: "<p>hi</p>",
                          extract_text=lambda t, c: [], now=NOW)
    assert empty.error == "no event found on this page"


def test_structured_event_outside_bay_area_is_an_error():
    page = JSONLD_PAGE.replace("San Francisco, CA", "Los Angeles, CA")
    r = links.resolve("https://venue.example.com/x", fetch=lambda u: page, extract_text=_no_llm, now=NOW)
    assert r.events == [] and r.error == "not in the Bay Area"
