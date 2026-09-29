"""Build realistic RFC 822 messages for ingest tests (replaced/augmented with
real captured .eml files after the live test — see the plan's Task 11)."""
from email.message import EmailMessage


def png_bytes(width=400, height=500, noise=True) -> bytes:
    """Noisy by default: solid-colour PNGs compress below the 10 KB 'tiny image'
    cutoff and would be filtered out."""
    from io import BytesIO
    from PIL import Image
    im = (Image.effect_noise((width, height), 64).convert("RGB") if noise
          else Image.new("RGB", (width, height), (40, 10, 60)))
    buf = BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def make_email(*, sender="Priya <priya@example.com>", subject="Event!", text=None, html=None,
               images=(), headers=None) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, "agora@example.com", subject
    msg["Message-ID"] = "<abc123@example.com>"
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.set_content(text or "")
    if html:
        msg.add_alternative(html, subtype="html")
    for name, mime, data in images:
        maintype, subtype = mime.split("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return bytes(msg)
