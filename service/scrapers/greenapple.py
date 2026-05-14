from dataclasses import dataclass
from datetime import datetime


@dataclass
class RawEvent:
    title: str
    start_time: datetime
    location: str | None
    url: str
    description: str | None


def scrape(url: str) -> list[RawEvent]:
    """Fetch and parse events from Green Apple Books."""
    raise NotImplementedError
