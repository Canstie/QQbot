"""Image validation shared by downloads and card renderers."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


def validate_dimensions(image: Image.Image, *, max_pixels: int = 40_000_000) -> None:
    width, height = image.size
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise ValueError("image dimensions are invalid")


def verify_image(path: Path, *, formats: set[str], max_pixels: int = 40_000_000) -> str:
    with Image.open(path) as image:
        validate_dimensions(image, max_pixels=max_pixels)
        image_format = image.format or ""
        if image_format not in formats:
            raise ValueError("unsupported image format")
        image.verify()
    return image_format
