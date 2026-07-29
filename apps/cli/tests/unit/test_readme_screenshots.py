from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[4]
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
