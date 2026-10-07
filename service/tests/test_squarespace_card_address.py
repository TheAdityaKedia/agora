from datetime import datetime
from pathlib import Path

import requests

from scrapers import glbthistory, maritime, sfcb, squarespace_events as sq

FIXTURE = Path(__file__).parent / "fixtures" / "squarespace_glbthistory.html"
BASE = "https://www.glbthistory.org/events"


def test_card_address_off_by_default():
    events = sq.parse_events(FIXTURE.read_text(), base_url=BASE, fallback_location="FALLBACK")
    assert {e.location for e in events} == {"FALLBACK"}


def test_card_address_uses_name_and_maps_street():
    events = sq.parse_events(FIXTURE.read_text(), base_url=BASE, fallback_location="FALLBACK",
                             card_address=True)
    by_title = {e.title: e.location for e in events}
    assert by_title["Reunion 2026"] == "The Westin St. Francis, 335 Powell Street San Francisco, CA, 94102"
    assert by_title["Workshop | Making Room: A Cyanotype Walk Through the Castro"] == \
        "GLBT Historical Society, 4127 18th Street San Francisco"


def test_card_address_drops_name_repeated_in_street():
    html = ('<article class="eventlist-event"><h1 class="eventlist-title">'
            '<a class="eventlist-title-link" href="/events/x">Chantey Sing</a></h1>'
            '<time class="event-date" datetime="2026-10-03"></time>'
            '<time class="event-time-localized-start">6:00 PM</time>'
            '<li class="eventlist-meta-address">Pier 45 <a class="eventlist-meta-address-maplink" '
            'href="http://maps.google.com?q=Pier 45 San Francisco United States">(map)</a></li>'
            '</article>')
    [ev] = sq.parse_events(html, base_url="https://maritime.org/events", card_address=True)
    assert ev.location == "Pier 45 San Francisco"


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def test_upcoming_only_drops_past_cards(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _Resp(FIXTURE.read_text()))
    all_events = sq.scrape_collection(BASE)
    upcoming = sq.scrape_collection(BASE, upcoming_only=True)
    assert len(all_events) == 3
    assert "Free Museum Day" not in [e.title for e in upcoming]  # a 2024 card
    today = datetime.now(sq.SOURCE_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    assert upcoming == [e for e in all_events if e.start_time >= today]


def test_wrappers_match():
    assert glbthistory.matches("https://www.glbthistory.org/events")
    assert sfcb.matches("https://www.sfcb.org/calendar")
    assert maritime.matches("https://maritime.org/events")
    assert not sfcb.matches("https://www.sfpl.org/events")


def _maritime_ev(title, location):
    from scrapers.base import RawEvent
    return RawEvent(title=title, start_time=datetime(2026, 10, 10), location=location,
                    url="u", description="d")


def test_maritime_places_each_event():
    assert maritime.place(_maritime_ev(
        "Fleet Week", "USS Pampanito and the Triangle at Historic Pier 45")).location == maritime.PIER_45
    assert maritime.place(_maritime_ev(
        "In-Person Chantey Sing – San Francisco", None)).location == maritime.MARITIME_MUSEUM
    # No address and no known venue: placed in the city only, never guessed.
    assert maritime.place(_maritime_ev("Annual Maritime Ball", None)).location == "San Francisco, CA"


def test_skip_paragraphs_drops_glbt_logistics_header():
    html = ('<article class="eventlist-event"><h1 class="eventlist-title">'
            '<a class="eventlist-title-link" href="/events/x">Talk</a></h1>'
            '<time class="event-date" datetime="2026-10-22"></time>'
            '<time class="event-time-localized-start">6:00 PM</time>'
            '<div class="eventlist-description">'
            '<p>LOCATION GLBT Historical Society Museum 4127 18th Street San Francisco, CA 94114</p>'
            '<p>ADMISSION $10; Free for Members</p><p>RSVP and reserve tickets here</p>'
            '<p>A panel discussion on queer historical fiction.</p></div></article>')
    [plain] = sq.parse_events(html, base_url=BASE)
    [clean] = sq.parse_events(html, base_url=BASE, skip_paragraphs=glbthistory.LOGISTICS_RE)
    assert plain.description.startswith("LOCATION")  # default unchanged
    assert clean.description == "A panel discussion on queer historical fiction."


def test_sfcb_drops_source_only_descriptions():
    from scrapers.base import RawEvent
    t = datetime(2026, 10, 14)
    events = [RawEvent(title="A", start_time=t, location="L", url="u",
                       description="Source: https://www.catranslation.org/event/passages-of-babel/"),
              RawEvent(title="B", start_time=t, location="L", url="u",
                       description="Source material: the artist's notebooks, shown for the first time.")]
    out = sfcb.drop_source_only(events)
    assert out[0].description is None
    assert out[1].description.startswith("Source material")
