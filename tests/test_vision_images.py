from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from qq_personal_bot import vision_images as vision


def encoded(image, format="PNG", **kwargs):
    output = BytesIO()
    image.save(output, format=format, **kwargs)
    return output.getvalue()


def test_small_image_is_preserved_without_upscaling():
    raw = encoded(Image.new("RGB", (320, 180), "blue"))
    prepared, mime = vision.prepare_vision_image(raw, "image/png")
    assert prepared == raw and mime == "image/png"


def test_large_noisy_image_has_bounded_upload_size_and_resolution():
    raw = encoded(Image.effect_noise((2600, 1800), 80).convert("RGB"))
    assert len(raw) > vision.MAX_IMAGE_BYTES
    prepared, mime = vision.prepare_vision_image(raw, "image/png")
    assert mime == "image/jpeg" and len(prepared) <= vision.MAX_IMAGE_BYTES
    assert len(prepared) < len(raw) / 2
    with Image.open(BytesIO(prepared)) as image:
        assert max(image.size) <= vision.MAX_IMAGE_SIDE
        assert image.width * image.height <= vision.MAX_IMAGE_PIXELS
        assert image.width / image.height == pytest.approx(2600 / 1800, abs=0.003)


def test_long_screenshot_gets_more_room_while_preserving_aspect_ratio():
    raw = encoded(Image.new("RGB", (800, 6000), "white"))
    prepared, _ = vision.prepare_vision_image(raw, "image/png")
    with Image.open(BytesIO(prepared)) as image:
        assert vision.MAX_IMAGE_SIDE < image.height <= vision.MAX_LONG_IMAGE_SIDE
        assert image.width / image.height == pytest.approx(800 / 6000, abs=0.001)


def test_transparent_image_keeps_visible_content_on_a_white_background():
    original = Image.new("RGBA", (2400, 1800), (0, 0, 0, 0))
    ImageDraw.Draw(original).rectangle((600, 450, 1800, 1350), fill="blue")
    prepared, _ = vision.prepare_vision_image(encoded(original), "image/png")
    with Image.open(BytesIO(prepared)) as image:
        assert all(channel > 245 for channel in image.getpixel((0, 0)))
        red, green, blue = image.getpixel((image.width // 2, image.height // 2))
        assert blue > 230 and red < 25 and green < 25


def test_resized_photo_respects_exif_orientation():
    image = Image.new("RGB", (3200, 1600), "red")
    exif = Image.Exif()
    exif[274] = 6
    prepared, _ = vision.prepare_vision_image(encoded(image, "JPEG", exif=exif), "image/jpeg")
    with Image.open(BytesIO(prepared)) as photo:
        assert photo.height == photo.width * 2
        assert photo.getexif().get(274, 1) == 1


def test_animation_is_not_silently_reduced_to_one_frame():
    first = Image.new("RGB", (80, 80), "red")
    second = Image.new("RGB", (80, 80), "blue")
    raw = encoded(first, "GIF", save_all=True, append_images=[second], duration=100, loop=0)
    prepared, mime = vision.prepare_vision_image(raw, "image/gif")
    assert prepared == raw and mime == "image/gif"
    with Image.open(BytesIO(prepared)) as image:
        assert image.n_frames == 2


def test_excessive_pixel_count_is_rejected_before_decoding(monkeypatch):
    raw = encoded(Image.new("RGB", (100, 100)))
    monkeypatch.setattr(vision, "MAX_INPUT_PIXELS", 1000)
    with pytest.raises(ValueError, match="pixel limit"):
        vision.prepare_vision_image(raw, "image/png")


def test_invalid_image_is_rejected():
    with pytest.raises(ValueError, match="cannot prepare"):
        vision.prepare_vision_image(b"not an image", "image/png")
