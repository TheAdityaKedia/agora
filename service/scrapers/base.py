from dataclasses import asdict, dataclass
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

    def to_dict(self) -> dict:
        """JSON-safe form, for handing events between processes (CI scrape → merge)."""
        d = asdict(self)
        d["start_time"] = self.start_time.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RawEvent":
        start = datetime.fromisoformat(d["start_time"])
        if start.tzinfo is None:
            raise ValueError(f"start_time must be timezone-aware: {d['start_time']!r}")
        return cls(**{**d, "start_time": start})
