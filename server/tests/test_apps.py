"""The family apps list behind the ▦ switcher: read once at startup, links stay on the
server's own release channel, and anything wrong with the file means no switcher."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from jarvis_live.apps import load_family_apps
from jarvis_live.config import Settings
from jarvis_live.db.models import OWNER_USER_ID
from tests.conftest import ServerHandle
from tests.work_item_helpers import SM, web_client

HOME = {
    "id": "home",
    "name": "Home",
    "live_url": "https://apj.dev",
    "lab_url": "https://lab.apj.dev",
}
JARVIS = {
    "id": "jarvis-live",
    "name": "Jarvis Live",
    "live_url": "https://live.apj.dev",
    "lab_url": "https://live.lab.apj.dev",
    "glance_url": "https://live.apj.dev/glance",
}
SLEEP = {"id": "sleep", "name": "Sleep Dashboard", "live_url": "https://sleep.apj.dev"}
ZED = {
    "id": "zed",
    "name": "Zed",
    "live_url": "https://zed.apj.dev",
    "lab_url": "https://zed.lab.apj.dev",
}


def write(tmp_path: Path, body: object) -> Path:
    p = tmp_path / "apps.json"
    p.write_text(body if isinstance(body, str) else json.dumps(body))
    return p


def load(path: Path | None, environment: str = "live") -> list[dict[str, object]]:
    return load_family_apps(Settings(apps_list_path=path, environment=environment))


def test_no_path_means_no_switcher() -> None:
    assert load(None) == []


def test_missing_file_fails_closed(tmp_path: Path) -> None:
    assert load(tmp_path / "nope.json") == []


@pytest.mark.parametrize("body", ["not json", "[1, 2]", '{"apps": "x"}', '{"apps": [1]}', "{}"])
def test_unreadable_list_fails_closed(tmp_path: Path, body: str) -> None:
    assert load(write(tmp_path, body)) == []


def test_live_links_to_each_apps_live_url(tmp_path: Path) -> None:
    apps = load(write(tmp_path, {"apps": [JARVIS, HOME]}), "live")
    assert [(a["id"], a["url"], a["available"]) for a in apps] == [
        ("home", "https://apj.dev", True),
        ("jarvis-live", "https://live.apj.dev", True),
    ]


def test_lab_links_to_each_apps_lab_url(tmp_path: Path) -> None:
    apps = load(write(tmp_path, {"apps": [HOME, JARVIS]}), "lab")
    assert [a["url"] for a in apps] == ["https://lab.apj.dev", "https://live.lab.apj.dev"]


def test_an_app_without_a_copy_on_this_channel_is_listed_but_not_linked(tmp_path: Path) -> None:
    sleep = next(a for a in load(write(tmp_path, {"apps": [SLEEP]}), "lab"))
    assert sleep["url"] is None and sleep["available"] is False


def test_home_first_then_apps_a_to_z(tmp_path: Path) -> None:
    apps = load(write(tmp_path, {"apps": [ZED, JARVIS, SLEEP, HOME]}))
    assert [a["id"] for a in apps] == ["home", "jarvis-live", "sleep", "zed"]


def test_other_channels_get_no_switcher(tmp_path: Path) -> None:
    assert load(write(tmp_path, {"apps": [HOME]}), "staging") == []


def test_urls_must_be_http(tmp_path: Path) -> None:
    bad = {**HOME, "live_url": "javascript:alert(1)"}
    apps = load(write(tmp_path, {"apps": [bad]}))
    assert apps[0]["url"] is None and apps[0]["available"] is False


def test_the_current_app_is_marked(tmp_path: Path) -> None:
    apps = load(write(tmp_path, {"apps": [HOME, JARVIS]}))
    assert [a["current"] for a in apps] == [False, True]


def test_the_shared_bare_list_reads_the_same(tmp_path: Path) -> None:
    # np-home-homelab's family-apps ConfigMap (docs/family-apps.md) is Home's shape: a bare list.
    apps = load(write(tmp_path, [ZED, JARVIS, HOME]))
    assert [(a["id"], a["current"]) for a in apps] == [
        ("home", False),
        ("jarvis-live", True),
        ("zed", False),
    ]


def test_an_entry_without_an_id_is_known_by_its_name(tmp_path: Path) -> None:
    # Home's own copy of the list has no ids: the name stands in, so Home and this app still match.
    bare = [{k: v for k, v in a.items() if k != "id"} for a in (SLEEP, JARVIS, HOME)]
    apps = load(write(tmp_path, bare))
    assert [(a["id"], a["current"]) for a in apps] == [
        ("home", False),
        ("jarvis-live", True),
        ("sleep-dashboard", False),
    ]


@pytest.fixture
def aserver(make_server: Callable[..., ServerHandle], tmp_path: Path) -> ServerHandle:
    return make_server(apps_list_path=write(tmp_path, {"apps": [HOME, JARVIS]}), environment="lab")


async def test_apps_endpoint_needs_sign_in_and_is_served_from_the_cache(
    aserver: ServerHandle, sm: SM, tmp_path: Path
) -> None:
    write(tmp_path, "garbage")  # the file changes after startup: the cache wins
    async with await web_client(aserver, sm, OWNER_USER_ID) as web:
        r = await web.get("/v1/apps")
        assert r.status_code == 200
        assert [a["url"] for a in r.json()["apps"]] == [
            "https://lab.apj.dev",
            "https://live.lab.apj.dev",
        ]
    import httpx

    async with httpx.AsyncClient(base_url=aserver.url) as anon:
        assert (await anon.get("/v1/apps")).status_code == 401
