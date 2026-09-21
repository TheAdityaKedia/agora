from dataclasses import dataclass
from datetime import datetime


@dataclass
class RawEvent:
    """A source-agnostic event as emitted by a scraper, before persistence.

    `start_time` must be timezone-aware (scrapers normalize source-local times
    to UTC). `id`, `sources`, and `created_at` are assigned at save time.
    `image_url` is optional (None for sources that don't expose an image, or
    for URL-less email/flyer submissions).
    """
    title: str
    start_time: datetime
    location: str | None
    url: str | None
    description: str | None
    image_url: str | None = None
