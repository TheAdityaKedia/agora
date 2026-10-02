"""Decide whether a submitted image can be the event's public picture, make
the public version, and upload it.

Only images safe as-is are published: a designed flyer with no personal
details or bystanders, a photo of a flyer cropped to the flyer, or the flyer
embedded in a screenshot (an Instagram post) cropped out — each re-checked.
A screenshot itself (chat, DM, app UI) never is — it carries other people's
names and numbers. Default is no image.
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


# Screenshots have crisp, axis-aligned edges around an embedded post image —
# unlike photos — so a rough model box can be snapped to them exactly.
EDGE_DIFF = 60          # summed RGB step that counts as an edge pixel
EDGE_COVERAGE = 0.6     # fraction of the box's span an edge line must cover
SNAP_SEARCH = 0.12      # search ± this fraction of the image around each side


def _longest_run(mask, lo: int, hi: int) -> tuple[int, int] | None:
    """(start, end) of the longest True run in mask that overlaps [lo, hi)."""
    best, start = None, None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if start < hi and i > lo and (best is None or i - start > best[1] - best[0]):
                best = (start, i)
            start = None
    return best


def snap_to_edges(im, box: dict) -> tuple[int, int, int, int]:
    """Pixel (left, top, right, bottom) for `box` (fractions), snapped to the
    embedded image's real rectangle when the screenshot has one.

    Left/right: the strong vertical edge lines nearest the box sides. Top/bottom:
    where those vertical edges start and stop — a rectangle's sides give its top
    and bottom, which avoids snapping to full-width app bars (headers, like bars).
    """
    import numpy as np
    w, h = im.size
    l, t, r, b = (int(float(box[k]) * d) for k, d in
                  (("left", w), ("top", h), ("right", w), ("bottom", h)))
    a = np.asarray(im.convert("RGB")).astype(np.int16)
    vert = np.abs(np.diff(a, axis=1)).sum(axis=2) > EDGE_DIFF        # (h, w-1)
    cols = vert[t:b].mean(axis=0)
    sx = int(SNAP_SEARCH * w)

    def nearest(target):
        lines = [i for i, f in enumerate(cols) if f >= EDGE_COVERAGE and abs(i + 1 - target) <= sx]
        return min(lines, key=lambda i: abs(i + 1 - target)) if lines else None

    il, ir = nearest(l), nearest(r)
    if il is None or ir is None or ir - il < 0.1 * w:
        return l, t, r, b
    runs = [_longest_run(vert[:, i], t, b) for i in (il, ir)]
    if None in runs:
        return l, t, r, b
    nt, nb = max(runs[0][0], runs[1][0]), min(runs[0][1], runs[1][1])
    if nb - nt < 0.1 * h:
        return l, t, r, b
    return il + 1, nt, ir + 1, nb


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
    if kind == "screenshot" and assessment.get("embedded_flyer_box"):
        # A screenshot of a public post (Instagram, an event page) around a
        # flyer: publish only the flyer, and only if the crop alone checks as a
        # clean designed flyer — no app UI, contact details, or people.
        # Boxes are coarse, so tighten over rounds like photos of flyers.
        box = assessment["embedded_flyer_box"]
        for _ in range(CROP_ROUNDS):
            try:
                cropped = im.crop(snap_to_edges(im, box))
            except (KeyError, TypeError, ValueError):
                return None
            out = _jpeg(cropped)
            check = verify(out)
            if not check:
                return None
            if check.get("kind") == "designed_flyer" and _clean(check):
                return out
            if check.get("kind") != "screenshot" or not check.get("embedded_flyer_box"):
                return None
            im, box = cropped, check["embedded_flyer_box"]
        return None
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
