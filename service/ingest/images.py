"""Decide whether a submitted image can be the event's public picture, make
the public version, and upload it.

Only images safe as-is are published: a designed flyer with no personal
details or bystanders, or a photo of a flyer cropped to the flyer and
re-checked. Screenshots (chats, DMs, apps) never are — they carry other
people's names and numbers. Default is no image.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
from typing import Callable

MAX_SIDE = 1200
JPEG_QUALITY = 82
BOX_PAD = 0.01  # a little margin around the model's flyer box
CROP_ROUNDS = 3


def _clean(a: dict | None) -> bool:
    return bool(a) and not a.get("personal_info_visible", True) and not a.get("bystanders_visible", True)


def _jpeg(im) -> bytes:
    im = im.convert("RGB")
    im.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = BytesIO()
    im.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)  # also drops EXIF (location data)
    return buf.getvalue()


def _crop(im, box: dict):
    try:
        l, t, r, b = (float(box[k]) for k in ("left", "top", "right", "bottom"))
    except (KeyError, TypeError, ValueError):
        return None
    l, t = max(0.0, l - BOX_PAD), max(0.0, t - BOX_PAD)
    r, b = min(1.0, r + BOX_PAD), min(1.0, b + BOX_PAD)
    if r - l < 0.1 or b - t < 0.1:
        return None
    w, h = im.size
    return im.crop((int(l * w), int(t * h), int(r * w), int(b * h)))


def public_version(data: bytes, assessment: dict | None,
                   verify: Callable[[bytes], dict | None]) -> bytes | None:
    """JPEG bytes safe to publish, or None. `verify(jpeg)` re-assesses a crop."""
    if not assessment:
        return None
    from PIL import Image
    try:
        im = Image.open(BytesIO(data))
        im.load()
    except Exception:
        return None
    kind = assessment.get("kind")
    if kind == "designed_flyer" and _clean(assessment):
        # Even a clean-looking flyer gets the stronger final check.
        out = _jpeg(im)
        check = verify(out)
        return out if check and check.get("kind") == "designed_flyer" and _clean(check) else None
    if kind == "photo_of_flyer" and assessment.get("flyer_box"):
        # Model boxes are coarse: crop, re-assess the crop (which also gives a
        # tighter box), and repeat — publish only a crop that checks clean.
        box = assessment["flyer_box"]
        for _ in range(CROP_ROUNDS):
            cropped = _crop(im, box)
            if cropped is None:
                return None
            out = _jpeg(cropped)
            check = verify(out)
            if not check:
                return None
            if check.get("kind") in ("designed_flyer", "photo_of_flyer") and _clean(check):
                return out
            if check.get("kind") != "photo_of_flyer" or not check.get("flyer_box"):
                return None
            im, box = cropped, check["flyer_box"]
    return None


def upload(data: bytes, *, put: Callable[[str, bytes], None], base_url: str) -> str:
    key = f"img/{hashlib.sha256(data).hexdigest()}.jpg"
    put(key, data)
    return f"{base_url.rstrip('/')}/{key}"
