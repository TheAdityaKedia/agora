from dataclasses import asdict, dataclass
from datetime import datetime


@dataclass
class RawEvent:
    """A source-agnostic event as emitted by a scraper, before persistence.

    `start_time` must be timezone-aware (scrapers normalize source-local times
    to UTC). `id`, `sources`, and `created_at` are assigned at save time.
    `image_url` is optional (None for sources that don't expose an image, or
    for URL-less email/flyer submissions). `status` is None unless the
    source flags the event cancelled or postponed.
    """
    title: str
    start_time: datetime
    location: str | None
    url: str | None
    description: str | None
    image_url: str | None = None
    # "cancelled" / "postponed" when the source says so (feature-specs/
    # event-lifecycle.md, §2): emit the event with it instead of dropping it.
    status: str | None = None

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
