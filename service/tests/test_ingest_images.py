from io import BytesIO

from PIL import Image

from ingest import images
from tests.emailfixtures import png_bytes

SAFE = {"kind": "designed_flyer", "personal_info_visible": False, "bystanders_visible": False,
        "flyer_box": None}


def _size(data):
    with Image.open(BytesIO(data)) as im:
        return im.size


def test_designed_flyer_without_personal_info_is_published_resized():
    out = images.public_version(png_bytes(1600, 2000), SAFE, verify=lambda b: SAFE)
    assert out is not None and max(_size(out)) <= images.MAX_SIDE


def test_designed_flyer_still_needs_the_final_check():
    assert images.public_version(png_bytes(), SAFE, verify=lambda b: None) is None
    assert images.public_version(png_bytes(), SAFE,
                                 verify=lambda b: dict(SAFE, bystanders_visible=True)) is None


def test_screenshots_and_personal_info_are_never_published():
    for a in (dict(SAFE, kind="screenshot"), dict(SAFE, personal_info_visible=True),
              dict(SAFE, bystanders_visible=True), dict(SAFE, kind="other"), None):
        assert images.public_version(png_bytes(), a, verify=lambda b: SAFE) is None


def test_photo_of_flyer_is_cropped_and_published_only_if_the_crop_verifies():
    photo = dict(SAFE, kind="photo_of_flyer", bystanders_visible=True,
                 flyer_box={"left": 0.2, "top": 0.2, "right": 0.8, "bottom": 0.8})
    seen = []

    def verify(crop):
        seen.append(_size(crop))
        return SAFE

    out = images.public_version(png_bytes(1000, 1000), photo, verify=verify)
    assert out is not None and seen and seen[0][0] < 1000  # the crop, not the photo, was checked
    unsafe = lambda crop: dict(SAFE, bystanders_visible=True)
    assert images.public_version(png_bytes(1000, 1000), photo, verify=unsafe) is None


def test_photo_of_flyer_crop_is_tightened_over_rounds():
    photo = dict(SAFE, kind="photo_of_flyer", bystanders_visible=True,
                 flyer_box={"left": 0.1, "top": 0.1, "right": 0.9, "bottom": 0.9})
    sizes = []
    replies = [dict(photo, flyer_box={"left": 0.1, "top": 0.1, "right": 0.9, "bottom": 0.9}), SAFE]
    # round 1: still has bystanders → tighten; round 2: clean → publish

    def verify(crop):
        sizes.append(_size(crop))
        return replies.pop(0)

    assert images.public_version(png_bytes(1000, 1000), photo, verify=verify) is not None
    assert len(sizes) == 2 and sizes[1][0] < sizes[0][0]  # second round is tighter


def test_photo_of_flyer_without_a_box_is_not_published():
    assert images.public_version(png_bytes(), dict(SAFE, kind="photo_of_flyer"),
                                 verify=lambda b: SAFE) is None


def test_bad_box_is_rejected():
    photo = dict(SAFE, kind="photo_of_flyer",
                 flyer_box={"left": 0.9, "top": 0.1, "right": 0.1, "bottom": 0.9})
    assert images.public_version(png_bytes(), photo, verify=lambda b: SAFE) is None


def test_upload_uses_a_content_hash_key_and_returns_the_public_url():
    puts = []
    url = images.upload(b"jpegbytes", put=lambda key, body: puts.append((key, body)),
                        base_url="https://d123.cloudfront.net")
    key = puts[0][0]
    assert key.startswith("img/") and key.endswith(".jpg") and len(key) == len("img/") + 64 + 4
    assert url == f"https://d123.cloudfront.net/{key}"


def test_flyer_embedded_in_a_screenshot_is_cropped_and_published_if_clean():
    """An Instagram post screenshot: app UI around a public flyer."""
    shot = dict(SAFE, kind="screenshot", personal_info_visible=True,
                embedded_flyer_box={"left": 0.05, "top": 0.05, "right": 0.95, "bottom": 0.65})
    seen = []

    def verify(crop):
        seen.append(_size(crop))
        return SAFE

    out = images.public_version(png_bytes(600, 1200), shot, verify=verify)
    assert out is not None and seen[0][1] < 1200 * 0.7  # only the flyer region was checked


def test_embedded_flyer_needs_a_clean_designed_flyer_verdict():
    shot = dict(SAFE, kind="screenshot",
                embedded_flyer_box={"left": 0.05, "top": 0.05, "right": 0.95, "bottom": 0.65})
    for verdict in (dict(SAFE, kind="screenshot"), dict(SAFE, personal_info_visible=True),
                    dict(SAFE, kind="photo_of_flyer"), None):
        assert images.public_version(png_bytes(600, 1200), shot, verify=lambda c, v=verdict: v) is None


def test_screenshot_without_an_embedded_flyer_is_never_published():
    shot = dict(SAFE, kind="screenshot", personal_info_visible=True)  # e.g. a WhatsApp text chat
    assert images.public_version(png_bytes(), shot, verify=lambda c: SAFE) is None


def test_embedded_flyer_crop_is_tightened_over_rounds():
    shot = dict(SAFE, kind="screenshot",
                embedded_flyer_box={"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 0.9})
    replies = [dict(SAFE, kind="screenshot",
                    embedded_flyer_box={"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 0.8}), SAFE]
    sizes = []

    def verify(crop):
        sizes.append(_size(crop))
        return replies.pop(0)

    assert images.public_version(png_bytes(600, 1200), shot, verify=verify) is not None
    assert len(sizes) == 2 and sizes[1][1] < sizes[0][1]


def _fake_screenshot():
    """App-like screenshot: dark UI, a header bar, a post image at
    (40, 120)-(560, 760), and a like bar + caption below it."""
    from PIL import ImageDraw
    im = Image.new("RGB", (600, 1100), (16, 16, 16))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 599, 90), fill=(40, 40, 40))           # app header
    d.rectangle((40, 120, 559, 759), fill=(130, 30, 50))       # the flyer
    d.text((80, 300), "WALKING TOUR", fill=(250, 200, 0))
    d.rectangle((40, 790, 300, 820), fill=(200, 200, 200))     # like bar
    buf = BytesIO(); im.save(buf, "PNG")
    return im, buf.getvalue()


def test_snap_to_edges_finds_the_embedded_image_from_a_rough_box():
    im, _ = _fake_screenshot()
    rough = {"left": 0.02, "top": 0.08, "right": 0.97, "bottom": 0.78}  # loose, includes like bar
    l, t, r, b = images.snap_to_edges(im, rough)
    assert abs(l - 40) <= 2 and abs(t - 120) <= 2 and abs(r - 560) <= 2 and abs(b - 760) <= 2


def test_snap_to_edges_keeps_the_box_when_there_are_no_straight_edges():
    im = Image.effect_noise((400, 400), 64).convert("RGB")
    box = {"left": 0.1, "top": 0.1, "right": 0.9, "bottom": 0.9}
    assert images.snap_to_edges(im, box) == (40, 40, 360, 360)


def test_screenshot_flyer_is_snapped_before_the_check():
    im, data = _fake_screenshot()
    shot = dict(SAFE, kind="screenshot",
                embedded_flyer_box={"left": 0.02, "top": 0.08, "right": 0.97, "bottom": 0.78})
    seen = []

    def verify(crop):
        seen.append(_size(crop))
        return SAFE

    assert images.public_version(data, shot, verify=verify) is not None
    w, h = seen[0]
    assert abs(w / h - 520 / 640) < 0.02  # the flyer's aspect, not the loose box's
