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
    assert out.failures == []  # b isn't an event page; a was, so b is incidental


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


def test_non_event_links_are_ignored_when_another_link_yields_an_event():
    """A signature link (personal site) in a link-first email must not turn a
    good submission into a failure reply."""
    text = "check this out https://blog.example.com/party\n\nPriya\nhttps://priya.example.com"
    inc = parse(make_email(text=text), uid="1")
    ex = fake_extract([[dict(GOOD)], []])
    out = run.process_message(inc, extract_fn=ex, fetch=lambda u: "<p>page</p>", now=NOW)
    assert len(out.events) == 1 and out.failures == []


def test_link_failures_reported_when_no_link_yields_an_event():
    inc = parse(make_email(text="https://a.example.com https://b.example.com"), uid="1")
    out = run.process_message(inc, extract_fn=fake_extract([[], []]), fetch=lambda u: "<p>x</p>", now=NOW)
    assert [u for u, _ in out.failures] == ["https://a.example.com", "https://b.example.com"]


def test_failure_log_line_is_privacy_safe():
    failures = [("Screenshot 2", "couldn't find a start time"),
                ("https://priya.example.com/about?x=1", "no event found on this page"),
                ('"Priya\'s secret party"', "this event already happened"),
                ("Your email", "no event found")]
    line = run.failure_summary(failures)
    assert "Screenshot 2: couldn't find a start time" in line
    assert "link (priya.example.com): no event found on this page" in line
    assert "an event: this event already happened" in line
    assert "secret" not in line and "/about" not in line


def test_signature_link_ignored_when_a_screenshot_yields_the_event():
    inc = parse(make_email(text="see flyers\nhttps://priya.example.com",
                           images=[("a.png", "image/png", png_bytes())]), uid="1")
    ex = fake_extract([[], [dict(GOOD)]])  # link page → nothing; screenshot → event
    out = run.process_message(inc, extract_fn=ex, fetch=lambda u: "<p>about me</p>", now=NOW)
    assert len(out.events) == 1 and out.failures == []


def test_long_email_also_resolves_known_platform_links(monkeypatch):
    """A newsletter's text may yield nothing while its Momence/Partiful links
    point straight at events — resolve those regardless of length."""
    text = "This week at the Alembic. " * 40 + (
        " https://momence.com/s/136417618 https://nav.example.com/home")
    inc = parse(make_email(text=text), uid="1")
    resolved = []

    def fake_resolve(url, **kw):
        resolved.append(url)
        from ingest.links import LinkResult
        return LinkResult(events=[__import__("scrapers.base", fromlist=["RawEvent"]).RawEvent(
            "Co-Working", NOW.replace(day=30), "Berkeley, CA", url, None)])

    monkeypatch.setattr(run.links_mod, "resolve", fake_resolve)
    out = run.process_message(inc, extract_fn=fake_extract([[]]), fetch=lambda u: "", now=NOW)
    assert resolved == ["https://momence.com/s/136417618"]  # not the nav link
    assert len(out.events) == 1 and out.failures == []


def test_link_first_falls_back_to_text_when_links_yield_nothing():
    """'Poetry night Fri 7pm at City Lights' + a blocked Instagram link: the
    event is in the text, so read it instead of failing on the link."""
    inc = parse(make_email(text="Poetry night Fri Oct 16 7:30pm at Borderlands https://instagram.com/p/abc"),
                uid="1")
    ex = fake_extract([[dict(GOOD)]])  # only the text call returns an event

    def blocked(url):
        raise RuntimeError("403")

    out = run.process_message(inc, extract_fn=ex, fetch=blocked, now=NOW)
    assert len(out.events) == 1 and out.failures == []
    assert ex.calls == [{"text": inc.text, "image": False, "context": ""}]


def test_bare_link_with_no_text_does_not_call_the_llm_on_text():
    inc = parse(make_email(text="https://instagram.com/p/abc"), uid="1")
    ex = fake_extract([])

    def blocked(url):
        raise RuntimeError("403")

    out = run.process_message(inc, extract_fn=ex, fetch=blocked, now=NOW)
    assert ex.calls == [] and out.failures == [("https://instagram.com/p/abc", "couldn't read this page")]


def test_screenshot_events_get_the_published_image_url():
    inc = parse(make_email(text="flyer!", images=[("f.png", "image/png", png_bytes())]), uid="1")
    ex = fake_extract([[]])  # text call: nothing
    published = []

    def image_fn(image):
        return [dict(GOOD)], {"kind": "designed_flyer"}

    def publish_fn(data, assessment):
        published.append(assessment["kind"])
        return "https://cdn.example.com/img/abc.jpg"

    out = run.process_message(inc, extract_fn=ex, fetch=lambda u: "", now=NOW,
                              image_fn=image_fn, publish_fn=publish_fn)
    assert published == ["designed_flyer"]
    assert [e.image_url for e in out.events] == ["https://cdn.example.com/img/abc.jpg"]


def test_image_not_published_when_it_yields_no_events():
    inc = parse(make_email(text="", images=[("f.png", "image/png", png_bytes())]), uid="1")
    calls = []
    run.process_message(inc, extract_fn=fake_extract([]), fetch=lambda u: "", now=NOW,
                        image_fn=lambda img: ([], {"kind": "designed_flyer"}),
                        publish_fn=lambda d, a: calls.append(1) or "u")
    assert calls == []
