from dataclasses import dataclass
from datetime import datetime


@dataclass
class RawEvent:
    """A source-agnostic event as emitted by a scraper, before persistence.

    `start_time` must be timezone-aware (scrapers normalize source-local times
    to UTC). `id`, `source`, and `created_at` are assigned at save time.
    """
    title: str
    start_time: datetime
    location: str | None
    url: str
    description: str | None
