from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ingest import SOURCE_NAME, quota, run
from ingest.message import parse
from models import Base
from tests.emailfixtures import make_email, png_bytes

NOW = datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc)
GOOD = {"title": "Verses & Vinyl", "date": "2026-10-16", "start_time": "19:30", "end_time": None,
        "venue": None, "address": "870 Valencia St, San Francisco", "description": None,
        "cost_text": None, "url": None, "recurrence": None}


def fake_extract(results):
    calls = []

    def fn(text=None, image=None, context=""):
        calls.append({"text": text, "image": image is not None, "context": context})
        return results.pop(0) if results else []
    fn.calls = calls
    return fn


def test_link_first_email_resolves_each_link_not_the_text():
    inc = parse(make_email(text="https://blog.example.com/a https://blog.example.com/b"), uid="1")
    ex = fake_extract([[dict(GOOD)], []])
    out = run.process_message(inc, extract_fn=ex, fetch=lambda u: "<p>page</p>", now=NOW)
    assert [c["context"] for c in ex.calls] == ["Page: https://blog.example.com/a",
                                                "Page: https://blog.example.com/b"]
    assert len(out.events) == 1
    assert out.failures == [("https://blog.example.com/b", "no event found on this page")]


def test_long_email_uses_text_extraction_once_and_images_each():
    text = "Newsletter intro. " * 40 + " https://nav.example.com/home"
    inc = parse(make_email(text=text, images=[("a.png", "image/png", png_bytes()),
                                             ("b.png", "image/png", png_bytes())]), uid="1")
    ex = fake_extract([[dict(GOOD)], [dict(GOOD, start_time=None)], []])
    out = run.process_message(inc, extract_fn=ex, fetch=lambda u: pytest.fail("no fetch"), now=NOW)
    assert [c["image"] for c in ex.calls] == [False, True, True]
    assert len(out.events) == 1
    assert ("Screenshot 1", "couldn't find a start time") in out.failures
    assert ("Screenshot 2", "no event found in this image") in out.failures


def test_empty_email_is_a_failure():
    inc = parse(make_email(text="hi!"), uid="1")
    out = run.process_message(inc, extract_fn=fake_extract([[]]), fetch=lambda u: "", now=NOW)
    assert out.events == [] and out.failures == [("Your email", "no event found")]


class FakeMail:
    address = "agora@example.com"

    def __init__(self, raws):
        self.raws, self.labels, self.sent = raws, {}, []

    def ensure_labels(self): pass
    def fetch_unprocessed(self, limit): return self.raws[:limit]
    def apply_label(self, uid, label): self.labels[uid] = label
    def send(self, msg): self.sent.append(msg)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_run_labels_saves_replies_and_counts(db):
    mail = FakeMail([("1", make_email(text="Verses & Vinyl Oct 16 7:30pm")),
                     ("2", make_email(text="hello", headers={"Auto-Submitted": "auto-replied"}))])
    saved_calls = []

    def save(raws, source):
        saved_calls.append((len(raws), source))
        return len(raws), 0, 0

    report = run.run_ingest(mail, extract_fn=fake_extract([[dict(GOOD)], []]), fetch=lambda u: "",
                            now=NOW, secret=b"k", session_factory=db, save=save)
    assert mail.labels == {"1": "agora/processed", "2": "agora/failed"}
    assert mail.sent == []  # #2 is auto-generated → no reply
    assert saved_calls == [(1, SOURCE_NAME)]
    assert report["saved"] == 1 and report["processed"] == 1 and report["failed"] == 1
    assert report["titles"] == ["Verses & Vinyl"]
    assert "priya" not in repr(report)  # no sender data in the report


def test_run_enforces_per_sender_daily_cap_and_replies(db):
    s = db()
    key = quota.sender_key("priya@example.com", b"k")
    quota.add(s, key, date(2026, 9, 29), 20)
    mail = FakeMail([("1", make_email(text="Verses & Vinyl Oct 16 7:30pm"))])
    report = run.run_ingest(mail, extract_fn=fake_extract([[dict(GOOD)]]), fetch=lambda u: "",
                            now=NOW, secret=b"k", session_factory=db, save=lambda r, source: (0, 0, 0))
    assert mail.labels == {"1": "agora/failed"} and report["saved"] == 0
    assert "daily limit" in mail.sent[0].get_content()
