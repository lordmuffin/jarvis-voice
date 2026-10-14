"""The family apps list behind the dashboard's ▦ switcher.

One shared JSON file lists the apps in the family (name, LIVE URL, LAB URL, optional glance
endpoint). It is read once at startup. Links stay on this server's release channel: LIVE
links to each app's LIVE URL, LAB to its LAB URL. Anything wrong with the file (unset,
missing, unreadable, malformed) or a channel that is neither means no switcher.
"""

import json
import logging
from typing import Any
from urllib.parse import urlsplit

from jarvis_live.config import Settings

log = logging.getLogger(__name__)

# This app's own id in the list, so the switcher can mark where you are.
SELF_ID = "jarvis-live"
HOME_ID = "home"
CHANNELS = ("live", "lab")


def _http_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parts = urlsplit(value)
    return value if parts.scheme in ("http", "https") and parts.netloc else None


def _read(settings: Settings) -> list[Any]:
    path = settings.apps_list_path
    if path is None:
        return []
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        log.warning("apps list %s unreadable, no family switcher: %s", path, e)
        return []
    apps = doc.get("apps") if isinstance(doc, dict) else None
    if not isinstance(apps, list) or not all(isinstance(a, dict) for a in apps):
        log.warning("apps list %s is malformed, no family switcher", path)
        return []
    return apps


def load_family_apps(settings: Settings) -> list[dict[str, object]]:
    """The switcher entries for this server's channel: Home first, then A-Z by name."""
    channel = settings.environment
    if channel not in CHANNELS:
        return []
    out: list[dict[str, object]] = []
    for a in _read(settings):
        app_id, name = a.get("id"), a.get("name")
        if not isinstance(app_id, str) or not isinstance(name, str) or not name:
            continue
        url = _http_url(a.get(f"{channel}_url"))
        out.append(
            {
                "id": app_id,
                "name": name,
                "url": url,
                "available": url is not None,
                "current": app_id == SELF_ID,
                "glance_url": _http_url(a.get("glance_url")),
            }
        )
    out.sort(key=lambda e: (e["id"] != HOME_ID, str(e["name"]).casefold()))
    return out
