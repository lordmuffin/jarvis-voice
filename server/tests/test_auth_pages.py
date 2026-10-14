"""The sign-in error pages: the Jarvis-Web-SignIn artboard, rendered by the server."""

import hashlib
import re
from pathlib import Path

import pytest

from jarvis_live.auth_pages import CSS_PATH, render_auth_page

REPO = Path(__file__).resolve().parents[2]
WEB_CSS = REPO / "web" / "src" / "instrument" / "web" / "instrument.css"


def page(kind: str = "expired", **kw: str) -> str:
    return render_auth_page(kind, app_name="Jarvis Live", channel="live", **kw)  # type: ignore[arg-type]


def text(html: str) -> str:
    """Visible text only: tags and the style block dropped."""
    html = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def test_expired_says_so_and_offers_a_fresh_sign_in() -> None:
    html = page("expired")
    assert "This sign-in link has expired." in text(html)
    assert "Please try again." in text(html)
    assert "Link expired" in text(html)
    assert re.search(r'<a [^>]*href="/auth/login"[^>]*>\s*Sign In Again\s*</a>', html)


def test_failed_names_the_failure_when_there_is_one() -> None:
    plain = page("failed")
    assert "Sign-in failed." in text(plain) and "Please try again." in text(plain)
    assert 'role="alert"' not in plain  # nothing to report beyond the title
    detailed = page("failed", detail="access_denied")
    assert 'role="alert"' in detailed and "access_denied" in text(detailed)
    assert 'href="/auth/login"' in detailed


def test_denied_names_the_account_and_offers_sign_out_and_home() -> None:
    html = page("denied", account="casey@apj.dev")
    assert "Your account (casey@apj.dev) is not allowed to use Jarvis Live." in text(html)
    assert "Ask an admin to add you." in text(html)
    assert re.search(r'<form[^>]*method="post"[^>]*action="/auth/logout"', html)
    assert "Sign Out" in text(html) and "Go to Home" in text(html)
    assert 'href="/"' in html
    assert "Sign In Again" not in text(html)


def test_the_app_name_is_a_parameter() -> None:
    html = render_auth_page("denied", app_name="Sleep Dashboard", channel="live", account="x")
    assert "not allowed to use Sleep Dashboard." in text(html)
    assert "Sleep Dashboard" in re.search(r"<title>(.*?)</title>", html, re.S).group(1)  # type: ignore[union-attr]
    assert "Jarvis Live" not in text(html)


@pytest.mark.parametrize(
    ("channel", "badge", "label"),
    [("live", "i-badge--live", "Live"), ("lab", "i-badge--lab", "Lab"), ("dev", None, "dev")],
)
def test_the_channel_badge_is_always_there(channel: str, badge: str | None, label: str) -> None:
    html = render_auth_page("expired", app_name="Jarvis Live", channel=channel)
    m = re.search(r'<span class="i-badge([^"]*)">([^<]*)</span>', html)
    assert m is not None and m.group(2) == label
    assert (badge in m.group(1)) if badge else m.group(1).strip() == ""


def test_everything_from_outside_is_escaped() -> None:
    evil = '<script>alert(1)</script>"&'
    for kind in ("denied", "failed"):
        html = page(kind, account=evil, detail=evil)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html
    html = render_auth_page("expired", app_name=evil, channel=evil, footer=evil)
    assert "<script>alert(1)</script>" not in html


def test_the_footer_is_shown_in_mono() -> None:
    html = page("expired", footer="live.apj.dev · server-v0.18.0 · 39e69a0")
    assert "live.apj.dev · server-v0.18.0 · 39e69a0" in text(html)


def test_the_page_is_self_contained_and_follows_the_system_theme() -> None:
    html = page("expired")
    assert not re.search(r'(src|href)="https?://', html) and "url(http" not in html
    assert "<script" not in html
    assert 'name="viewport"' in html and 'name="color-scheme"' in html
    assert 'class="inst' in html
    assert "prefers-color-scheme: dark" in html  # from Instrument's own CSS


def test_the_embedded_css_is_the_vendored_instrument_css() -> None:
    assert "--inst-accent" in CSS_PATH.read_text()
    if not WEB_CSS.exists():  # the server image is built from server/ alone
        pytest.skip("web/ is not in this checkout")
    assert (
        hashlib.sha256(CSS_PATH.read_bytes()).hexdigest()
        == hashlib.sha256(WEB_CSS.read_bytes()).hexdigest()
    ), "auth_pages/instrument.css drifted from web/src/instrument; copy it again"
