from ingest import mailbox, message
from tests.emailfixtures import make_email

OWN = "agora.sf.events@gmail.com"


def inc(**kw):
    return message.parse(make_email(**kw), uid="9")


def test_should_reply_rules():
    assert mailbox.should_reply(inc(text="x"), OWN)
    assert not mailbox.should_reply(inc(text="x", headers={"Auto-Submitted": "auto-replied"}), OWN)
    assert not mailbox.should_reply(inc(text="x", sender="MAILER-DAEMON@google.com"), OWN)
    assert not mailbox.should_reply(inc(text="x", sender="No-Reply <no-reply@shop.example.com>"), OWN)
    assert not mailbox.should_reply(inc(text="x", sender=f"Agora <{OWN}>"), OWN)


def test_failure_reply_threads_and_lists_reasons():
    msg = mailbox.compose_failure_reply(
        inc(text="x"), own_address=OWN,
        failures=[("https://x.example.com/e", "couldn't read this page"),
                  ("Screenshot 2", "couldn't find a start time")])
    assert msg["To"] == "priya@example.com" and msg["From"] == OWN
    assert msg["In-Reply-To"] == "<abc123@example.com>" and msg["References"] == "<abc123@example.com>"
    assert msg["Subject"].startswith("Re:") and msg["Auto-Submitted"] == "auto-replied"
    body = msg.get_content()
    assert "https://x.example.com/e — couldn't read this page" in body
    assert "Screenshot 2 — couldn't find a start time" in body


class FakeIMAP:
    def __init__(self):
        self.calls = []

    def login(self, *a): self.calls.append(("login",))
    def select(self, box): self.calls.append(("select", box)); return "OK", [b"3"]
    def create(self, name): self.calls.append(("create", name)); return "OK", []
    def logout(self): self.calls.append(("logout",))

    def uid(self, cmd, *args):
        self.calls.append(("uid", cmd) + args)
        if cmd == "SEARCH":
            return "OK", [b"5 3 9"]
        if cmd == "FETCH":
            return "OK", [(b"x", b"raw-" + args[0].encode())]
        return "OK", []


def test_gmail_fetches_unlabeled_oldest_first_and_labels(monkeypatch):
    fake = FakeIMAP()
    monkeypatch.setattr(mailbox.imaplib, "IMAP4_SSL", lambda host: fake)
    with mailbox.Gmail(OWN, "pw") as g:
        g.ensure_labels()
        msgs = g.fetch_unprocessed(limit=2)
        g.apply_label("3", "agora/processed")
    assert [u for u, _ in msgs] == ["3", "5"] and msgs[0][1] == b"raw-3"
    search = next(c for c in fake.calls if c[:2] == ("uid", "SEARCH"))
    assert "-label:agora-processed" in search[-1] and "-label:agora-failed" in search[-1]
    assert ("uid", "STORE", "3", "+X-GM-LABELS", '("agora/processed")') in fake.calls
    assert sum(1 for c in fake.calls if c[0] == "create") == 3
