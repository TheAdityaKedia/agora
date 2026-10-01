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
