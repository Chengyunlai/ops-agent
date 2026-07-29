import re
from pathlib import Path
from xml.etree import ElementTree

REPOSITORY_ROOT = Path(__file__).parents[4]
README_CONTENT_WIDTH_PX = 960
MINIMUM_READABLE_FONT_SIZE_PX = 12
SCREENSHOTS = (
    REPOSITORY_ROOT / "docs/images/tui-overview.svg",
    REPOSITORY_ROOT / "docs/images/tui-pods.svg",
    REPOSITORY_ROOT / "docs/images/tui-settings.svg",
)


def test_readme_embeds_sanitized_demo_screenshots() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    for screenshot in SCREENSHOTS:
        assert f"docs/images/{screenshot.name}" in readme
        svg = screenshot.read_text(encoding="utf-8")
        assert svg.startswith("<svg")
        assert "sample-app" in svg
        assert all(
            sensitive not in svg.lower()
            for sensitive in (
                "cyrus",
                "customer-name",
                "deepseek_api_key",
                "sk-",
            )
        )


def test_readme_screenshots_remain_readable_when_scaled() -> None:
    for screenshot in SCREENSHOTS:
        svg = screenshot.read_text(encoding="utf-8")
        viewbox_match = re.search(r'viewBox="0 0 ([\d.]+) [\d.]+"', svg)
        font_size_match = re.search(r"font-size: ([\d.]+)px;", svg)
        assert viewbox_match is not None
        assert font_size_match is not None

        source_width = float(viewbox_match.group(1))
        source_font_size = float(font_size_match.group(1))
        rendered_font_size = source_font_size * min(
            1,
            README_CONTENT_WIDTH_PX / source_width,
        )

        assert rendered_font_size >= MINIMUM_READABLE_FONT_SIZE_PX


def test_readme_screenshots_scale_wide_glyphs_without_overlap() -> None:
    for screenshot in SCREENSHOTS:
        root = ElementTree.parse(screenshot).getroot()
        fixed_length_text = [
            element
            for element in root.iter("{http://www.w3.org/2000/svg}text")
            if "textLength" in element.attrib
        ]

        assert fixed_length_text
        assert all(
            element.get("lengthAdjust") == "spacingAndGlyphs"
            for element in fixed_length_text
        )
