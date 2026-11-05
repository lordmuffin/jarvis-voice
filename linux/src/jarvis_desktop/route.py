"""Headphones only: may a reply be spoken where the default sink plays it?

docs/plans/voice-push-to-talk.md, "Headphones only". Jarvis speaks only into headphones or a
headset: the analog jack, a USB headset, or Bluetooth on A2DP or HFP. Never built-in speakers,
HDMI or DisplayPort, and never anything unknown. The decision is a pure function on PipeWire's
own description of the default sink (``pw-dump``: the sink node, its device and the device's
active output route), and anything missing, unexpected or unparsable fails closed.

Residual risk (the plan's decision 3): a Bluetooth speaker or speakerphone that reports itself
as a headset reads as one. PipeWire does say ``speaker`` for many, which is never closed."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

#: Runs a command and returns its stdout; raises ``OSError`` if it can't.
Run = Callable[..., Awaitable[bytes]]

_HEADPHONE_PORTS = frozenset({"headphones", "headset"})
_HEADPHONE_FORMS = frozenset({"headset", "headphone"})
_SPEAKER_FORMS = frozenset({"speaker", "internal"})
#: PipeWire's Bluetooth sink profiles for A2DP (any codec) and HFP/HSP.
_BLUETOOTH_PROFILES = ("a2dp-sink", "headset-head-unit")


class Route(Enum):
    HEADPHONES = "headphones"  # the analog jack, with something plugged in
    USB_HEADSET = "usb_headset"
    BLUETOOTH = "bluetooth"
    SPEAKERS = "speakers"
    HDMI = "hdmi"  # and DisplayPort
    UNKNOWN = "unknown"

    @property
    def closed(self) -> bool:
        """Only the listener can hear it: the reply may be spoken."""
        return self in (Route.HEADPHONES, Route.USB_HEADSET, Route.BLUETOOTH)


@dataclass(frozen=True)
class Sink:
    """What PipeWire says about one sink, as far as the gate cares."""

    name: str
    api: str | None  # device.api: alsa, bluez5
    bus: str | None  # device.bus: pci, usb, bluetooth
    form_factor: str | None  # device.form-factor
    profile: str | None  # the Bluetooth profile, or the ALSA card profile
    port_type: str | None  # the active output route's port.type
    port_available: str | None  # yes, no or unknown (jack detection)


def default_sink(dump: object) -> Sink | None:
    """The default sink in a ``pw-dump``; None if the dump doesn't say, or isn't one."""
    if not isinstance(dump, list):
        return None
    objects = [o for o in dump if isinstance(o, dict)]
    name = _default_sink_name(objects)
    if name is None:
        return None
    node = next(
        (
            o
            for o in objects
            if _props(o).get("media.class") == "Audio/Sink" and _props(o).get("node.name") == name
        ),
        None,
    )
    if node is None:
        return None
    props = _props(node)
    dev = next((o for o in objects if o.get("id") == props.get("device.id")), None)
    dev_props = _props(dev) if dev is not None else {}

    def get(key: str) -> str | None:
        value = props.get(key, dev_props.get(key))
        return value if isinstance(value, str) else None

    route = _output_route(dev, props.get("card.profile.device")) if dev is not None else None
    return Sink(
        name=name,
        api=get("device.api"),
        bus=get("device.bus"),
        form_factor=get("device.form-factor"),
        profile=get("api.bluez5.profile") or get("device.profile.name"),
        port_type=_port_type(route) if route is not None else None,
        port_available=_str(route.get("available")) if route is not None else None,
    )


def classify(sink: Sink | None) -> Route:
    if sink is None:
        return Route.UNKNOWN
    if sink.port_type == "hdmi" or "hdmi" in (sink.profile or ""):
        return Route.HDMI
    if sink.form_factor in _SPEAKER_FORMS or sink.port_type == "speaker":
        return Route.SPEAKERS
    if sink.api == "bluez5":
        if sink.form_factor in _HEADPHONE_FORMS and (sink.profile or "").startswith(
            _BLUETOOTH_PROFILES
        ):
            return Route.BLUETOOTH
        return Route.UNKNOWN
    if sink.api != "alsa":
        return Route.UNKNOWN
    plugged = sink.port_type in _HEADPHONE_PORTS and sink.port_available == "yes"
    if sink.bus == "usb":
        return (
            Route.USB_HEADSET
            if plugged or sink.form_factor in _HEADPHONE_FORMS
            else (Route.UNKNOWN)
        )
    return Route.HEADPHONES if plugged else Route.UNKNOWN


async def read_route(run: Run | None = None) -> tuple[Route, Sink | None]:
    """The default sink's route now. Fails closed if ``pw-dump`` is missing or says nothing."""
    try:
        out = await (run or _run)("pw-dump")
        sink = default_sink(json.loads(out))
    except (OSError, ValueError):
        return Route.UNKNOWN, None
    return classify(sink), sink


async def _run(*argv: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        raise OSError(f"{argv[0]} exited with {proc.returncode}")
    return out


def _props(obj: dict[str, Any]) -> dict[str, Any]:
    info = obj.get("info")
    props = info.get("props") if isinstance(info, dict) else None
    return props if isinstance(props, dict) else {}


def _default_sink_name(objects: list[dict[str, Any]]) -> str | None:
    for o in objects:
        if o.get("type") != "PipeWire:Interface:Metadata":
            continue
        props = o.get("props")
        if not isinstance(props, dict) or props.get("metadata.name") != "default":
            continue
        entries = o.get("metadata")
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("key") == "default.audio.sink":
                value = entry.get("value")
                name = value.get("name") if isinstance(value, dict) else None
                return name if isinstance(name, str) else None
    return None


def _output_route(dev: dict[str, Any], profile_device: object) -> dict[str, Any] | None:
    """The device's active output route that feeds this sink."""
    info = dev.get("info")
    params = info.get("params") if isinstance(info, dict) else None
    routes = params.get("Route") if isinstance(params, dict) else None
    for r in routes if isinstance(routes, list) else []:
        if (
            isinstance(r, dict)
            and r.get("direction") == "Output"
            and r.get("device") == profile_device
        ):
            return r
    return None


def _port_type(route: dict[str, Any]) -> str | None:
    """``port.type`` from a route's info: a count followed by key, value pairs."""
    info = route.get("info")
    if not isinstance(info, list):
        return None
    pairs = info[1:]
    for key, value in zip(pairs[::2], pairs[1::2], strict=False):
        if key == "port.type":
            return _str(value)
    return None


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None
