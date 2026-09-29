"""Email ingest orchestration: fetch → parse → extract → validate → save →
label → reply. CLI: `python -m ingest.run --report PATH [--outputs PATH]`."""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

from ingest import (LOCAL_TZ, MAX_EMAILS_PER_RUN, MAX_EVENTS_PER_EMAIL,
                    MAX_EVENTS_PER_SENDER_PER_DAY, MAX_LINKS_PER_EMAIL, SOURCE_NAME, quota)
from ingest import links as links_mod
from ingest import validate
from ingest.mailbox import compose_failure_reply, should_reply
from ingest.message import _URL_RE, Incoming, is_link_first, parse
from ingest.message import diagnostics as message_diagnostics
from scrapers.base import RawEvent
from scrapers.browser import BROWSER_UA

NO_EVENT_TEXT = "no event found"
NO_EVENT_IMAGE = "no event found in this image"
OVER_LIMIT = "over the daily limit of 20 events per sender — try again tomorrow"


@dataclass
class Outcome:
    events: list[RawEvent] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)


def _log(msg: str) -> None:
    print(f"[ingest] {msg}", flush=True)  # never log sender, subject, or body


def _safe_label(label: str) -> str:
    """A failure label fit for public logs: screenshot numbers and the email
    itself as-is, links reduced to their domain, event titles hidden."""
    if label.startswith(("Screenshot", "Your email", "Some events")):
        return label
    if label.startswith("http"):
        from urllib.parse import urlparse
        return f"link ({urlparse(label).netloc})"
    return "an event"


def failure_summary(failures: list[tuple[str, str]]) -> str:
    return "; ".join(f"{_safe_label(label)}: {reason}" for label, reason in failures)


def _from_candidates(cands: list[dict], label_for, now, url=None) -> Outcome:
    out = Outcome()
    for c in cands:
        events, reason = validate.candidate_to_events(c, now=now, url=url)
        if reason:
            out.failures.append((label_for(c), reason))
        out.events += events
    return out


def process_message(inc: Incoming, *, extract_fn: Callable, fetch: Callable[[str], str],
                    now: datetime) -> Outcome:
    out = Outcome()
    title_of = lambda c: f'"{c.get("title") or "an event"}"'
    not_events = []  # links that aren't event pages (signatures, personal sites…)
    if is_link_first(inc) and inc.links:
        for url in inc.links[:MAX_LINKS_PER_EMAIL]:
            r = links_mod.resolve(url, fetch=fetch, now=now,
                                  extract_text=lambda t, ctx: extract_fn(text=t, context=ctx))
            if r.error in (links_mod.NO_EVENT, links_mod.UNREADABLE):
                not_events.append((url, r.error))
            elif r.error:
                out.failures.append((url, r.error))
            out.events += r.events
            sub = _from_candidates(r.candidates, lambda c: url, now)
            out.events += sub.events
            out.failures += sub.failures
        # Links gave nothing (blocked Instagram post, homepage) but the email
        # also says something — the event may be in the text.
        if not out.events and len(_URL_RE.sub("", inc.text).strip()) >= MIN_FALLBACK_TEXT_CHARS:
            sub = _from_candidates(extract_fn(text=inc.text), title_of, now)
            out.events += sub.events
            out.failures += sub.failures
    elif inc.text.strip():
        # Long email (a newsletter): known-platform links are exact and cheap,
        # so resolve those first; their failures are incidental (past sessions,
        # sold-out pages) and never reported.
        from_links: list[RawEvent] = []
        for url in [u for u in inc.links if links_mod.is_known_platform(u)][:MAX_KNOWN_LINKS]:
            r = links_mod.resolve(url, fetch=fetch, now=now,
                                  extract_text=lambda t, ctx: extract_fn(text=t, context=ctx))
            from_links += r.events
        cands = extract_fn(text=inc.text)
        sub = _from_candidates(cands, title_of, now)
        out.events += from_links + _not_already(sub.events, from_links)
        out.failures += sub.failures
        if not cands and not from_links and not inc.images:
            out.failures.append(("Your email", NO_EVENT_TEXT))
    for i, image in enumerate(inc.images, 1):
        cands = extract_fn(image=image)
        if not cands:
            out.failures.append((f"Screenshot {i}", NO_EVENT_IMAGE))
            continue
        sub = _from_candidates(cands, lambda c, i=i: f"Screenshot {i}", now)
        out.events += sub.events
        out.failures += sub.failures
    # Report non-event links only if nothing in the whole email (links, text
    # or screenshots) was an event — otherwise they're incidental.
    if not out.events:
        out.failures += not_events
    if not out.events and not out.failures:
        out.failures.append(("Your email", NO_EVENT_TEXT))
    return out


MAX_KNOWN_LINKS = 20
MIN_FALLBACK_TEXT_CHARS = 20  # "Poetry night Fri 7pm" is enough to be worth a read


def _title_key(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", title.lower())[:3])


def _not_already(events: list[RawEvent], known: list[RawEvent]) -> list[RawEvent]:
    """Drop text-extracted events a link already produced (same start, same
    first three title words) — the link version is exact."""
    seen = {(e.start_time, _title_key(e.title)) for e in known}
    return [e for e in events if (e.start_time, _title_key(e.title)) not in seen]


def _cap(events: list[RawEvent], n: int) -> list[RawEvent]:
    """Keep the first n distinct events (occurrences of one event count once)."""
    kept, titles = [], []
    for e in events:
        if e.title not in titles:
            if len(titles) >= n:
                continue
            titles.append(e.title)
        kept.append(e)
    return kept


def run_ingest(mail, *, extract_fn, fetch, now: datetime, secret: bytes, session_factory,
               save: Callable, limit: int = MAX_EMAILS_PER_RUN) -> dict:
    report = {"emails": 0, "processed": 0, "partial": 0, "failed": 0, "replies": 0,
              "saved": 0, "merged": 0, "skipped": 0, "titles": []}
    today = now.astimezone(LOCAL_TZ).date()
    session = session_factory()
    try:
        quota.prune(session, today)
        mail.ensure_labels()
        for uid, raw in mail.fetch_unprocessed(limit):
            report["emails"] += 1
            inc = parse(raw, uid=uid)
            out = Outcome() if inc.auto_generated else \
                process_message(inc, extract_fn=extract_fn, fetch=fetch, now=now)
            if inc.auto_generated:
                out.failures.append(("Your email", "automatic reply ignored"))
            key = quota.sender_key(inc.sender, secret)
            allowed = max(0, min(MAX_EVENTS_PER_EMAIL,
                                 MAX_EVENTS_PER_SENDER_PER_DAY - quota.used_today(session, key, today)))
            kept = _cap(out.events, allowed)
            if len({e.title for e in out.events}) > len({e.title for e in kept}):
                out.failures.append(("Some events", OVER_LIMIT))
            if kept:
                saved, merged, skipped = save(kept, source=SOURCE_NAME)
                report["saved"] += saved
                report["merged"] += merged
                report["skipped"] += skipped
                quota.add(session, key, today, len({e.title for e in kept}))
                report["titles"] += sorted({e.title for e in kept})
            label = ("agora/processed" if kept and not out.failures else
                     "agora/partial" if kept else "agora/failed")
            report[label.split("/")[1]] += 1
            _log(f"email {report['emails']}: {label} ({message_diagnostics(inc)})"
                 + (f" — {failure_summary(out.failures)}" if out.failures else ""))
            if out.failures and should_reply(inc, mail.address):
                mail.send(compose_failure_reply(inc, own_address=mail.address, failures=out.failures))
                report["replies"] += 1
            mail.apply_label(uid, label)
    finally:
        session.close()
    return report


def _fetch(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=25)
    resp.raise_for_status()
    return resp.text


def cli(argv=None) -> None:
    import classify
    import main as pipeline
    from db import get_session
    from ingest import extract
    from ingest.mailbox import Gmail

    parser = argparse.ArgumentParser(description="Ingest event submissions from the Gmail inbox.")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--outputs", type=Path, default=None)
    args = parser.parse_args(argv)

    pipeline.init_db()
    client = classify.make_client()
    now = datetime.now(timezone.utc)
    extract_fn = lambda text=None, image=None, context="": extract.extract_events(
        client, text=text, image=image, now=now, context=context)
    with Gmail(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"]) as mail:
        report = run_ingest(mail, extract_fn=extract_fn, fetch=_fetch, now=now,
                            secret=os.environ["SUBMISSION_HASH_KEY"].encode(),
                            session_factory=get_session, save=pipeline.save_events)
    _log(f"{report['emails']} emails: {report['processed']} processed, {report['partial']} partial, "
         f"{report['failed']} failed, {report['replies']} replies; {report['saved']} saved, "
         f"{report['merged']} merged, {report['skipped']} skipped")
    for t in report["titles"]:
        _log(f"published: {t}")
    exported = 0
    if report["saved"] or report["merged"]:
        try:
            pipeline.classify_upcoming(source_names={SOURCE_NAME})
        except Exception as e:
            _log(f"classify skipped ({type(e).__name__}: {e})")
        out = Path(os.environ.get("EVENTS_JSON_PATH", pipeline.DEFAULT_EVENTS_JSON))
        exported = pipeline.export_json(out)
    # Merge-style report so ci.py guard / the ship action can render the PR body.
    args.report.write_text(json.dumps({"exported": exported, "failed": 0, "sources": [{
        "url": "email", "name": SOURCE_NAME, "status": "ok", "events": report["saved"] + report["merged"],
        "saved": report["saved"], "merged": report["merged"], "skipped": report["skipped"], "error": None}]}))
    if args.outputs:
        with open(args.outputs, "a") as f:
            f.write(f"saved={report['saved'] + report['merged']}\n")


if __name__ == "__main__":
    cli()
