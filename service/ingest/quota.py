"""Per-sender daily cap without storing addresses: a keyed HMAC is the key."""
import hashlib
import hmac
from datetime import date, timedelta

from models import SubmissionCount


def sender_key(address: str, secret: bytes) -> str:
    return hmac.new(secret, address.strip().lower().encode(), hashlib.sha256).hexdigest()


def used_today(session, key: str, day: date) -> int:
    row = session.get(SubmissionCount, (key, day))
    return row.events if row else 0


def add(session, key: str, day: date, n: int) -> None:
    row = session.get(SubmissionCount, (key, day))
    if row is None:
        session.add(SubmissionCount(sender_key=key, day=day, events=n))
    else:
        row.events += n
    session.commit()


def prune(session, today: date, keep_days: int = 7) -> int:
    cutoff = today - timedelta(days=keep_days)
    n = session.query(SubmissionCount).filter(SubmissionCount.day < cutoff).delete()
    session.commit()
    return n
