"""Gmail over IMAP/SMTP with an app password: read unprocessed mail, label it,
send failure replies. No Google Cloud project / OAuth token expiry."""
from __future__ import annotations

import imaplib
import smtplib
from email.message import EmailMessage

from ingest.message import Incoming

LABELS = ("agora/processed", "agora/partial", "agora/failed")
_SEARCH = 'X-GM-RAW "' + " ".join(f"-label:{l.replace('/', '-')}" for l in LABELS) + ' in:inbox"'
_NO_REPLY_LOCALS = {"mailer-daemon", "postmaster", "no-reply", "noreply", "donotreply", "do-not-reply"}


def should_reply(inc: Incoming, own_address: str) -> bool:
    local = inc.sender.split("@", 1)[0]
    return bool(inc.sender) and not inc.auto_generated and \
        inc.sender != own_address.lower() and local not in _NO_REPLY_LOCALS


def compose_failure_reply(inc: Incoming, *, own_address: str,
                          failures: list[tuple[str, str]]) -> EmailMessage:
    msg = EmailMessage()
    msg["From"], msg["To"] = own_address, inc.sender
    msg["Subject"] = "Re: your Agora submission"
    if inc.message_id:
        msg["In-Reply-To"] = msg["References"] = inc.message_id
    msg["Auto-Submitted"] = "auto-replied"  # so other auto-responders don't answer us
    lines = ["Thanks for sending this to Agora. Some of it couldn't be added:", ""]
    lines += [f"- {label} — {reason}" for label, reason in failures]
    lines += ["", "Anything else in your email was added. Reply with the missing details "
                  "and we'll try again."]
    msg.set_content("\n".join(lines))
    return msg


class Gmail:
    def __init__(self, address: str, password: str, imap_host: str = "imap.gmail.com",
                 smtp_host: str = "smtp.gmail.com"):
        self.address, self._password = address, password
        self._imap_host, self._smtp_host = imap_host, smtp_host
        self._imap = None

    def __enter__(self):
        self._imap = imaplib.IMAP4_SSL(self._imap_host)
        self._imap.login(self.address, self._password)
        self._imap.select("INBOX")
        return self

    def __exit__(self, *exc):
        try:
            self._imap.logout()
        finally:
            self._imap = None

    def ensure_labels(self) -> None:
        for label in LABELS:
            self._imap.create(f'"{label}"')  # "already exists" is fine

    def fetch_unprocessed(self, limit: int) -> list[tuple[str, bytes]]:
        _, data = self._imap.uid("SEARCH", None, _SEARCH)
        uids = sorted((u.decode() for u in (data[0] or b"").split()), key=int)[:limit]
        out = []
        for uid in uids:
            _, parts = self._imap.uid("FETCH", uid, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
            if raw:
                out.append((uid, raw))
        return out

    def apply_label(self, uid: str, label: str) -> None:
        self._imap.uid("STORE", uid, "+X-GM-LABELS", f'("{label}")')

    def send(self, msg: EmailMessage) -> None:
        with smtplib.SMTP_SSL(self._smtp_host, 465) as smtp:
            smtp.login(self.address, self._password)
            smtp.send_message(msg)
