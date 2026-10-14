"""Sign-in error pages in the family design system (Instrument).

The server answers a failed browser sign-in with a page, not a line of text. The markup follows
the ``Jarvis-Web-SignIn`` artboard: the wordmark and channel badge, what happened, one clear
action. ``app_name`` is a parameter so another app in the family can use the same page.

``instrument.css`` here is a copy of ``web/src/instrument/web/instrument.css`` (the managed,
vendored Instrument stylesheet); ``tests/test_auth_pages.py`` fails if the two drift. The page
is self-contained (no scripts, no external requests) and follows the system light/dark setting.
"""

from html import escape
from pathlib import Path
from typing import Literal

CSS_PATH = Path(__file__).with_name("instrument.css")

Kind = Literal["expired", "failed", "denied"]

# Layout only; every colour and size below comes from Instrument's own tokens.
_PAGE_CSS = """
html, body { margin: 0; background: var(--inst-bg); }
.auth {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 16px;
}
.auth-card {
  background: var(--inst-surface);
  width: 100%;
  max-width: 340px;
  min-height: 300px;
  padding: 28px 24px 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.auth-brand { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.auth-title { font-size: 16px; font-weight: 700; line-height: 1.4; }
.auth-tag--warn { color: var(--inst-warning); }
.auth-tag--error { color: var(--inst-error); }
.auth-foot { margin-top: auto; }
.auth form { margin: 0; }
/* Instrument v0.1.0 colours every link `.inst a` (accent), which outranks `.i-btn--*`, so a link
   that is a button would be accent on accent. The same fix web/src/ds/ds.css carries; drop both
   when Instrument fixes the specificity. */
.inst .i-btn--primary { color: var(--inst-on-accent); }
.inst .i-btn--secondary { color: var(--inst-text); }
.inst .i-btn--quiet { color: var(--inst-text-2); }
.inst .i-btn--quiet:hover { color: var(--inst-text); }
"""

# kind -> (tag, tag class, title, line, action)
_COPY = {
    "expired": (
        "Link expired",
        "auth-tag--warn",
        "This sign-in link has expired.",
        "Please try again.",
    ),
    "failed": ("Sign-in failed", "auth-tag--error", "Sign-in failed.", "Please try again."),
    "denied": ("Not allowed", "auth-tag--error", "", "Ask an admin to add you."),
}


def _badge(channel: str) -> str:
    cls = {"live": " i-badge--live", "lab": " i-badge--lab"}.get(channel, "")
    label = {"live": "Live", "lab": "Lab"}.get(channel, channel)
    return f'<span class="i-badge{cls}">{escape(label)}</span>'


def render_auth_page(
    kind: Kind,
    *,
    app_name: str,
    channel: str,
    footer: str = "",
    account: str = "",
    detail: str = "",
) -> str:
    """A full HTML page for a sign-in outcome. Every value is escaped."""
    tag, tag_cls, title, line = _COPY[kind]
    if kind == "denied":
        title = f"Your account ({account}) is not allowed to use {app_name}."
    banner = ""
    if kind == "failed" and detail:
        banner = (
            '<div class="i-banner i-banner--error" role="alert">'
            f"<b>✕</b><span>{escape(detail)}</span></div>"
        )
    if kind == "denied":
        actions = (
            '<form method="post" action="/auth/logout">'
            '<button type="submit" class="i-btn i-btn--secondary">Sign Out</button></form>'
            '<a href="/" class="i-btn i-btn--quiet">Go to Home</a>'
        )
    else:
        actions = '<a href="/auth/login" class="i-btn i-btn--primary">Sign In Again</a>'
    foot = f'<span class="t-mono t-micro t-2 auth-foot">{escape(footer)}</span>' if footer else ""
    css = CSS_PATH.read_text(encoding="utf-8")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{escape(app_name)} · Sign-in</title>
<style>{css}{_PAGE_CSS}</style>
</head>
<body>
<main class="inst auth">
<div class="auth-card">
<div class="auth-brand"><span class="t-title">{escape(app_name)}</span>{_badge(channel)}</div>
<div class="i-stack">
<span class="t-tag {tag_cls}">{tag}</span>
<h1 class="auth-title">{escape(title)}</h1>
<p class="t-caption t-2">{escape(line)}</p>
</div>
{banner}
<div class="i-row">{actions}</div>
{foot}
</div>
</main>
</body>
</html>
"""
