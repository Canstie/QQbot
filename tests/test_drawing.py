from __future__ import annotations

import pytest
from PIL import Image, ImageDraw, ImageFont

from qq_personal_bot import drawing


@pytest.fixture(autouse=True)
def reset_resolved_fonts():
    drawing._fontconfig_fallback.cache_clear()
    yield
    drawing._fontconfig_fallback.cache_clear()


@pytest.mark.parametrize("text,expected", [
    ("A👩‍💻B", ["A", "👩‍💻", "B"]),
    ("👍🏽🇨🇳🇯🇵", ["👍🏽", "🇨🇳", "🇯🇵"]),
    ("1️⃣☀️é", ["1️⃣", "☀️", "é"]),
    ("🏳️‍🌈", ["🏳️‍🌈"]),
])
def test_emoji_clusters_remain_whole(text, expected):
    assert drawing.grapheme_clusters(text) == expected


def test_real_emoji_uses_a_supported_fallback_instead_of_missing_glyph():
    primary = ImageFont.load_default(size=32)
    fonts = drawing.fallback_fonts(32)
    if not any(drawing.font_supports(font, "😊") for font in fonts):
        pytest.skip("Host has no emoji font")
    runs = drawing.font_runs("ABC😊", primary)
    assert runs[0] == ("ABC", primary)
    emoji, selected = runs[-1]
    assert emoji == "😊" and selected is not primary
    missing = selected.getmask("\U0010ffff", mode="L")
    actual = selected.getmask(emoji, mode="L")
    assert actual.size != missing.size or bytes(actual) != bytes(missing)


def test_fallback_measurement_matches_painted_bounds_and_centers_emoji():
    image = Image.new("RGB", (260, 90), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=32)
    text = "Hello 😊"
    bounds = drawing.text_bounds_with_fallback(draw, text, font)
    drawing.draw_centered_text_with_fallback(draw, text, box=(0, 0, 260, 90), font=font, fill="black")
    actual = image.convert("RGB")
    from PIL import ImageChops

    painted = ImageChops.difference(actual, Image.new("RGB", image.size, "white")).getbbox()
    assert painted is not None
    # Font bboxes can include a few blank raster-padding rows, especially COLR emoji.
    assert abs((painted[1] + painted[3]) / 2 - 45) <= 2
    assert abs((painted[3] - painted[1]) - (bounds[3] - bounds[1])) <= 4


def test_fallback_draw_aligns_different_fonts_on_one_baseline(monkeypatch):
    primary = ImageFont.load_default(size=32)
    alternate = ImageFont.load_default(size=20)
    monkeypatch.setattr(drawing, "font_runs", lambda text, font: [("a", primary), ("b", alternate)])
    calls = []

    class Draw:
        def text(self, position, text, **kwargs):
            calls.append((position, kwargs))

        def textlength(self, text, **kwargs):
            return 20.5

    drawing.draw_text_with_fallback(Draw(), (5, 7), "ab", font=primary, fill="black")
    assert calls[0][0] == (5, 7 + primary.getmetrics()[0])
    assert calls[1][0] == (25.5, calls[0][0][1])
    assert all(kwargs["anchor"] == "ls" and kwargs["embedded_color"] for _, kwargs in calls)


def test_unusual_nickname_symbol_resolves_an_installed_font_without_shell(monkeypatch):
    from types import SimpleNamespace

    primary, fallback = object(), object()
    calls = []
    monkeypatch.setattr(drawing, "_font_size", lambda font: 30)
    monkeypatch.setattr(drawing, "fallback_fonts", lambda size: ())
    monkeypatch.setattr(drawing, "font_supports", lambda font, text: font is fallback)
    monkeypatch.setattr(drawing.shutil, "which", lambda name: "/usr/bin/fc-match")
    monkeypatch.setattr(drawing, "_resolved_font", lambda *args: fallback)

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(stdout="/usr/share/fonts/NotoSansJavanese.ttf\n0\n")

    monkeypatch.setattr(drawing.subprocess, "run", run)
    assert drawing.font_runs("꧁꧂", primary) == [("꧁꧂", fallback)]
    assert calls[0][0][-1] == ":charset=a9c1"
    assert calls[0][1]["timeout"] == 1
    assert not calls[0][1].get("shell", False)


def test_missing_fontconfig_keeps_card_rendering_available(monkeypatch):
    primary = object()
    monkeypatch.setattr(drawing, "_font_size", lambda font: 30)
    monkeypatch.setattr(drawing, "fallback_fonts", lambda size: ())
    monkeypatch.setattr(drawing, "font_supports", lambda *args: False)
    monkeypatch.setattr(drawing.shutil, "which", lambda name: None)
    assert drawing.font_runs("꧁", primary) == [("꧁", primary)]
