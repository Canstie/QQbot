from __future__ import annotations

import logging
import math
import warnings
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 1024 * 1024
MAX_IMAGE_PIXELS = 3_000_000
MAX_INPUT_PIXELS = 40_000_000
MAX_IMAGE_SIDE = 2048
MAX_LONG_IMAGE_SIDE = 4096


def prepare_vision_image(body: bytes, content_type: str) -> tuple[bytes, str]:
    """Prepare an in-memory inference copy; never overwrite the source image."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(body)) as image:
                original_size = image.size
                width, height = original_size
                if width * height > MAX_INPUT_PIXELS:
                    raise ValueError("image exceeds vision pixel limit")
                animated = bool(getattr(image, "is_animated", False))
                max_side = MAX_LONG_IMAGE_SIDE if max(width, height) >= min(width, height) * 3 else MAX_IMAGE_SIDE
                scale = min(1.0, max_side / max(width, height), math.sqrt(MAX_IMAGE_PIXELS / (width * height)))
                if animated or (scale == 1.0 and len(body) <= MAX_IMAGE_BYTES):
                    # Retain animation and small originals; JPEG conversion would lose frames.
                    prepared, mime = body, content_type
                    final_size = original_size
                else:
                    size = (max(1, int(width * scale)), max(1, int(height * scale)))
                    image.thumbnail(size, Image.Resampling.LANCZOS, reducing_gap=3.0)
                    frame = ImageOps.exif_transpose(image)
                    if frame.mode in {"RGBA", "LA"} or "transparency" in frame.info:
                        rgba = frame.convert("RGBA")
                        rgb = Image.new("RGB", rgba.size, "white")
                        rgb.paste(rgba, mask=rgba.getchannel("A"))
                    else:
                        rgb = frame.convert("RGB")
                    prepared = _bounded_jpeg(rgb)
                    mime, final_size = "image/jpeg", rgb.size
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("cannot prepare vision image") from exc
    logger.info(
        "Vision image input_bytes=%s output_bytes=%s input_size=%sx%s output_size=%sx%s animated=%s",
        len(body), len(prepared), *original_size, *final_size, animated,
    )
    return prepared, mime


def _bounded_jpeg(image: Image.Image) -> bytes:
    for _ in range(6):
        for quality in (85, 75, 65):
            output = BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            if output.tell() <= MAX_IMAGE_BYTES:
                return output.getvalue()
        image.thumbnail((max(1, int(image.width * 0.8)), max(1, int(image.height * 0.8))),
                        Image.Resampling.LANCZOS)
    raise ValueError("image exceeds prepared vision size limit")
