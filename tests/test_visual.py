"""Image checks. Uses Pillow only to build test images; the header reader itself is pure Python."""
import io

import pytest

from app.pipeline import visual

PIL = pytest.importorskip("PIL.Image")


def make(fmt, size=(40, 30), colour=(49, 92, 232)):
    buf = io.BytesIO()
    PIL.new("RGB", size, colour).save(buf, fmt)
    return buf.getvalue()


@pytest.mark.parametrize("fmt,name", [("PNG", "png"), ("JPEG", "jpeg"), ("GIF", "gif"), ("WEBP", "webp")])
def test_sniff_reads_format_and_size(fmt, name):
    assert visual.sniff_image(make(fmt)) == {"format": name, "width": 40, "height": 30}


def test_sniff_rejects_other_files():
    for junk in (b"", b"hello world, not an image at all", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", b"%PDF-1.7 ......"):
        with pytest.raises(ValueError):
            visual.sniff_image(junk)


def test_alt_text_rules():
    assert visual.alt_problem("") and visual.alt_problem("   ") and visual.alt_problem("image") and visual.alt_problem("banner.png")
    assert visual.alt_problem("IMG_2041") and visual.alt_problem("hi")
    assert visual.alt_problem("A blue Zippy Bottle on a wooden table") is None


PALETTE = visual.approved_palette(["Approved colours: Primary #315CE8, Ink #111827, Accent #A5B4FC, Canvas #F6F7FB."])


def test_palette_accepts_brand_colours_and_neutrals():
    assert visual.analyze(make("PNG", colour=(49, 92, 232)), PALETTE)["palette_issue"] is None  # brand blue
    assert visual.analyze(make("PNG", colour=(255, 255, 255)), PALETTE)["palette_issue"] is None  # white
    assert visual.analyze(make("PNG", colour=(20, 20, 20)), PALETTE)["palette_issue"] is None  # near black


def test_palette_flags_a_large_off_brand_area():
    info = visual.analyze(make("PNG", colour=(228, 87, 46)), PALETTE)  # orange
    assert info["palette_checked"] is True and info["palette_issue"]["share"] >= 0.9 and info["palette_issue"]["severity"] == "medium"
    assert info["palette_issue"]["colour"].startswith("#")


def test_small_off_brand_accent_is_not_flagged():
    im = PIL.new("RGB", (100, 100), (49, 92, 232))
    for x in range(100):
        for y in range(8):
            im.putpixel((x, y), (228, 87, 46))  # 8% orange strip
    buf = io.BytesIO(); im.save(buf, "PNG")
    assert visual.analyze(buf.getvalue(), PALETTE)["palette_issue"] is None


def test_no_palette_means_no_colour_finding():
    assert visual.analyze(make("PNG", colour=(228, 87, 46)), set())["palette_issue"] is None
