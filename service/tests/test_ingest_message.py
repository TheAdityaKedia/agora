from ingest import message
from tests.emailfixtures import make_email, png_bytes


def test_parse_plain_text_sender_and_links():
    raw = make_email(text="Come to Poetry Night!\nhttps://partiful.com/e/79v5MvdmXJreSkcvfI4n.\n"
                          "-- \nPriya · https://priya.example.com")
    inc = message.parse(raw, uid="7")
    assert inc.uid == "7" and inc.sender == "priya@example.com"
    assert inc.message_id == "<abc123@example.com>"
    assert "Poetry Night" in inc.text and "priya.example.com" not in inc.text  # signature cut
    assert inc.links == ["https://partiful.com/e/79v5MvdmXJreSkcvfI4n"]  # trailing '.' stripped


def test_parse_html_only_and_unwraps_safelinks_and_drops_unsubscribe():
    html = ('<p>Show on Friday</p>'
            '<a href="https://www.google.com/url?q=https%3A%2F%2Fvenue.example.com%2Fshow&sa=D">tix</a>'
            '<a href="https://list.example.com/unsubscribe?u=1">unsubscribe</a>')
    inc = message.parse(make_email(text="", html=html), uid="1")
    assert "Show on Friday" in inc.text
    assert inc.links == ["https://venue.example.com/show"]


def test_parse_keeps_forwarded_body_but_drops_quoted_reply():
    text = ("Look at these!\n\n---------- Forwarded message ---------\nFrom: SF Weekly\n"
            "Jazz on Oct 12 at 8pm, SFJAZZ.\n\nOn Mon, Sep 28, 2026 at 9:00 AM Someone wrote:\n> old stuff")
    inc = message.parse(make_email(text=text), uid="1")
    assert "Jazz on Oct 12" in inc.text and "old stuff" not in inc.text


def test_parse_images_filters_tiny_oversized_and_caps_at_five():
    big = png_bytes(900, 1200)
    imgs = [(f"f{i}.png", "image/png", big) for i in range(6)]
    imgs.append(("logo.png", "image/png", png_bytes(50, 50)))       # tiny → ignored
    imgs.append(("huge.png", "image/png", b"\x89PNG" + b"0" * (11 * 1024 * 1024)))  # >10 MB → ignored
    inc = message.parse(make_email(text="flyers", images=imgs), uid="1")
    assert len(inc.images) == 5 and all(m == "image/png" for m, _ in inc.images)


def test_auto_generated_detection():
    auto = message.parse(make_email(text="x", headers={"Auto-Submitted": "auto-replied"}), uid="1")
    bulk = message.parse(make_email(text="x", headers={"Precedence": "bulk"}), uid="2")
    normal = message.parse(make_email(text="x"), uid="3")
    assert auto.auto_generated and bulk.auto_generated and not normal.auto_generated


def test_is_link_first():
    short = message.parse(make_email(text="check this out https://partiful.com/e/abc"), uid="1")
    long = message.parse(make_email(text="Newsletter " * 80 + " https://x.example.com/e"), uid="2")
    assert message.is_link_first(short) and not message.is_link_first(long)
