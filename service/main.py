from pathlib import Path

from db import init_db
from scrapers import greenapple


SOURCES_FILE = Path(__file__).parent / "data" / "sources.txt"


def load_sources() -> list[str]:
    return [line.strip() for line in SOURCES_FILE.read_text().splitlines() if line.strip()]


def run():
    init_db()
    sources = load_sources()
    for url in sources:
        if "greenapplebooks.com" in url:
            events = greenapple.scrape(url)
            print(f"[greenapple] found {len(events)} events")


if __name__ == "__main__":
    run()
