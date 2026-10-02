import pytest

from datetime import datetime, timezone
from pathlib import Path

from scrapers import faight

HTML = (Path(__file__).parent / "fixtures" / "faight_events.html").read_text()
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def test_matches_events_page_only():
    assert faight.matches("https://www.thefaight.com/events")
    assert not faight.matches("https://partiful.com/explore/sf")


def test_parse_skips_private_events():
    titles = [e.title for e in faight.parse(HTML, now=NOW)]
    assert "Private Booking" not in titles
    assert titles == ["Proxima Parada ft. Zoë Winter", "Blue Lemonade - Live @ The Faight", "Open Mic"]


def test_event_fields():
    e = faight.parse(HTML, now=NOW)[1]
    assert e.start_time == datetime(2026, 10, 3, 2, 0, tzinfo=timezone.utc)  # Fri Oct 2, 7pm PT
    assert e.location == "The Faight Collective, 475 Haight St, San Francisco, CA 94117"
    assert e.url == "https://www.eventbrite.com/e/blue-lemonade-live-the-faight-tickets-2001901486235"
    assert e.description.startswith("Blue Lemonade live at The Faight")
    assert e.image_url == ("https://cdn.sanity.io/images/3l1powkg/production/"
                           "6b4c2796390e6bdefb0edc84ff7b335db4d68e72-2000x1336.jpg"
                           "?w=800&fit=max&auto=format")


def test_tracking_params_stripped_and_missing_ticket_link_falls_back_to_the_card():
    events = faight.parse(HTML, now=NOW)
    assert all("aff=" not in (e.url or "") for e in events)
    assert events[2].url == "https://www.thefaight.com/events#open-mic"


def test_past_events_dropped():
    assert faight.parse(HTML, now=datetime(2026, 10, 6, tzinfo=timezone.utc))[0].title == "Open Mic"


def test_page_without_data_returns_nothing():
    assert faight.parse("<html><body>maintenance</body></html>", now=NOW) == []


# --- image sizing -----------------------------------------------------------

def test_poster_urls_ask_the_cdn_to_resize():
    """Full-size posters are 157 KB–2.7 MB; the site shows 72x72 thumbnails."""
    url = faight.parse(HTML, now=NOW)[1].image_url
    assert url.endswith("?w=800&fit=max&auto=format")
    assert "6b4c2796390e6bdefb0edc84ff7b335db4d68e72-2000x1336.jpg" in url


# --- description enrichment -------------------------------------------------

EB_HTML = """<html><body><script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"context":{"structuredContent":{"modules":[
 {"type":"text","text":"<p>Doors at 7. <b>Three bands</b> and a DJ set, plus a late-night jam in the gallery.</p>"},
 {"type":"text","text":"<p>All ages, $15 at the door.</p>"}]}}}}}
</script></body></html>"""


def test_thin_descriptions_are_enriched_from_the_ticket_page():
    """The venue's own blurbs are often a placeholder line ("Live at the
    Faight!"); the Eventbrite page carries the organizer's full text."""
    events = faight.parse(HTML, now=NOW)
    thin = events[1]  # Blue Lemonade: 70-char blurb
    assert len(thin.description) < faight.THIN_DESCRIPTION_CHARS
    fetched = []
    out = faight.enrich([thin], fetch=lambda u: fetched.append(u) or EB_HTML)
    assert out[0].description.startswith("Doors at 7.") and "late-night jam" in out[0].description
    assert fetched == [thin.url]


def test_events_without_a_ticket_page_are_not_fetched():
    ticketless = next(e for e in faight.parse(HTML, now=NOW) if e.title == "Open Mic")
    assert faight.enrich([ticketless], fetch=lambda u: pytest.fail("should not fetch")) == [ticketless]


def test_rich_descriptions_are_left_alone():
    rich = faight.parse(HTML, now=NOW)[1]
    rich.description = "x" * (faight.THIN_DESCRIPTION_CHARS + 1)
    out = faight.enrich([rich], fetch=lambda u: pytest.fail("should not fetch"))
    assert out[0].description == rich.description


def test_enrich_survives_a_failing_ticket_page():
    def boom(url):
        raise RuntimeError("503")

    before = faight.parse(HTML, now=NOW)
    after = faight.enrich(faight.parse(HTML, now=NOW), fetch=boom)
    assert [e.description for e in after] == [e.description for e in before]
