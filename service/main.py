from datetime import datetime, timezone
from pathlib import Path

from db import init_db, get_session
from models import Event
from scrapers import greenapple
from scrapers.greenapple import RawEvent


SOURCES_FILE = Path(__file__).parent / "data" / "sources.txt"


def load_sources() -> list[str]:
    return [line.strip() for line in SOURCES_FILE.read_text().splitlines() if line.strip()]


def save_events(raw_events: list[RawEvent], source: str) -> tuple[int, int]:
    """Persist raw events to the database, skipping duplicates by URL.

    Returns (saved, skipped) counts.
    """
    session = get_session()
    saved = skipped = 0
    try:
        for raw in raw_events:
            exists = session.query(Event).filter_by(url=raw.url).first()
            if exists:
                skipped += 1
                continue
            session.add(Event(
                title=raw.title,
                start_time=raw.start_time,
                location=raw.location,
                url=raw.url,
                description=raw.description,
                source=source,
                created_at=datetime.now(timezone.utc),
            ))
            saved += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return saved, skipped


def scrape_and_save(url: str) -> None:
    if "greenapplebooks.com" in url:
        raw_events = greenapple.scrape(url)
        saved, skipped = save_events(raw_events, source="greenapplebooks.com")
        print(f"[greenapple] {saved} saved, {skipped} skipped")
    else:
        print(f"[warn] no scraper for {url}")


def run():
    init_db()
    for url in load_sources():
        scrape_and_save(url)


if __name__ == "__main__":
    run()
