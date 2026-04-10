from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read_text(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_help_page_exists_and_covers_core_product_topics() -> None:
    help_html = _read_text("help", "index.html")

    assert "Help and FAQ" in help_html
    assert "Everything admins and members need" in help_html
    assert 'href="#media"' in help_html
    assert "Route and media behavior" in help_html
    assert "/birthdayadmin setup" in help_html
    assert "/birthdayadmin test-message" in help_html
    assert "{anniversary.years}" in help_html
    assert "{server_anniversary.years_since_creation}" in help_html
    assert "https://discord.com/servers/inevitable-friendship-1322933864360050688" in help_html
    assert "https://github.com/arno-create/Bdayblaze" in help_html


def test_homepage_and_legal_pages_link_help_and_use_real_banner_asset() -> None:
    index_html = _read_text("index.html")
    privacy_html = _read_text("privacy", "index.html")
    terms_html = _read_text("terms", "index.html")

    assert "./help/" in index_html
    assert "../help/" in privacy_html
    assert "../help/" in terms_html
    assert "assets/banner.jpg" in index_html
    assert "assets/banner.jpg" in privacy_html
    assert "assets/banner.jpg" in terms_html
    assert "banner.png" not in index_html
    assert "banner.png" not in privacy_html
    assert "banner.png" not in terms_html
    assert "/birthdayadmin" in index_html
    assert "/birthdayadmin member remove" in privacy_html


def test_sitemap_and_readme_include_help_page() -> None:
    sitemap_xml = _read_text("sitemap.xml")
    readme = _read_text("README.md")

    assert "https://arno-create.github.io/Bdayblaze/help/" in sitemap_xml
    assert "[Help and FAQ](https://arno-create.github.io/Bdayblaze/help/)" in readme
    assert "assets/banner.jpg" in readme
    assert "/birthdayadmin setup" in readme
