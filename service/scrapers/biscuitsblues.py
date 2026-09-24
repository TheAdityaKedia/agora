"""Biscuits & Blues events scraper (via its Squarespace events collection).

Biscuits & Blues is a Union Square (SF) blues club. Its homepage loads the
Eventbrite widget (eb_widgets.js), but that's only per-show *checkout* — each
show card carries an Eventbrite event id used to pop the ticket modal. There is
no clean Eventbrite organizer page (the ids don't resolve as standalone
/e/<id> pages), and pointing users at an Eventbrite deep link is a worse landing
page than the venue's own show page.

The actual schedule lives in a Squarespace Events Collection at /find-a-show —
a server-rendered list of ``article.eventlist-event`` cards (one per showing)
with the title, date + start time, a venue-hosted permalink, and a thumbnail.
That's the top of the data-source ladder available here, so this is a thin
wrapper around scrapers/squarespace_events.py.

The collection cards expose an ``.eventlist-excerpt`` blurb but no
``.eventlist-description`` (which the shared parser reads), so descriptions come
back empty from the listing alone. We enable ``enrich_descriptions`` so the lib
fetches each show's detail page for the full synopsis
(``.eventitem-column-content``).
"""
from scrapers import squarespace_events
from scrapers.base import RawEvent


SOURCE = "biscuitsandblues.com"
NAME = "Biscuits & Blues"
CALENDAR_URL = "https://www.biscuitsandblues.com/find-a-show"
ADDRESS = "Biscuits & Blues, 401 Mason St, San Francisco, CA 94102"


def matches(url: str) -> bool:
    return "biscuitsandblues.com" in url


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    events = squarespace_events.scrape_collection(
        CALENDAR_URL,
        fallback_location=ADDRESS,
        enrich_descriptions=True,
    )
    print(f"[biscuitsblues] done: {len(events)} events", flush=True)
    return events
