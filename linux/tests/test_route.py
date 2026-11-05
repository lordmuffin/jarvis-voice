"""The headphones-only gate, on PipeWire's own description of the default sink.

Three fixtures are real ``pw-dump`` output from the development machine (the default-sink
metadata, the sink node and its device, with serials and addresses removed): a USB audio
interface with a headphone socket, a Bluetooth speakerphone on A2DP, and HDMI. The other cases
are those fixtures edited to the shape PipeWire gives the device in question."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from jarvis_desktop.route import Route, Sink, classify, default_sink, read_route

FIXTURES = Path(__file__).parent / "fixtures" / "pipewire"


def dump(name: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


def route_of(objects: object) -> Route:
    return classify(default_sink(objects))


def of_class(objects: list[dict[str, Any]], media_class: str) -> dict[str, Any]:
    return next(
        o for o in objects if o.get("info", {}).get("props", {}).get("media.class") == media_class
    )


def node(objects: list[dict[str, Any]]) -> dict[str, Any]:
    props: dict[str, Any] = of_class(objects, "Audio/Sink")["info"]["props"]
    return props


def device(objects: list[dict[str, Any]]) -> dict[str, Any]:
    return of_class(objects, "Audio/Device")


def output_route(objects: list[dict[str, Any]]) -> dict[str, Any]:
    routes = device(objects)["info"]["params"]["Route"]
    found: dict[str, Any] = next(r for r in routes if r["direction"] == "Output")
    return found


def set_port(objects: list[dict[str, Any]], port_type: str, available: str = "yes") -> None:
    r = output_route(objects)
    info = r["info"]
    i = info.index("port.type")
    info[i + 1] = port_type
    r["available"] = available


def wired_jack(port_type: str = "headphones", available: str = "yes") -> list[dict[str, Any]]:
    """The HDMI controller's sink reshaped as an on-board analog codec: a PCI card whose active
    output route is the headphone jack, as ACP reports a laptop's or desktop's jack."""
    objects = copy.deepcopy(dump("hdmi"))
    props = node(objects)
    props["device.profile.name"] = "analog-stereo"
    props["node.name"] = "alsa_output.pci-0000_00_1f.3.analog-stereo"
    objects[0]["metadata"][0]["value"]["name"] = props["node.name"]
    set_port(objects, port_type, available)
    output_route(objects)["name"] = f"analog-output-{port_type}"
    return objects


# --- the real fixtures ---------------------------------------------------------------------


def test_reads_the_default_sink_from_the_dump() -> None:
    sink = default_sink(dump("bluetooth_a2dp"))
    assert sink == Sink(
        name="bluez_output.00_00_00_00_00_00.1",
        api="bluez5",
        bus="bluetooth",
        form_factor="headset",
        profile="a2dp-sink",
        port_type="headset",
        port_available="yes",
    )


def test_a_bluetooth_a2dp_headset_is_closed() -> None:
    """Residual risk, decision 3: this one is a speakerphone that calls itself a headset."""
    assert route_of(dump("bluetooth_a2dp")) is Route.BLUETOOTH
    assert Route.BLUETOOTH.closed


def test_hdmi_is_never_closed() -> None:
    assert route_of(dump("hdmi")) is Route.HDMI
    assert not Route.HDMI.closed


def test_a_usb_interface_that_does_not_say_headset_is_unknown() -> None:
    """A USB mic or interface with a headphone socket, but also line outs to monitors: PipeWire
    gives no form factor and an analog port, so it fails closed."""
    assert route_of(dump("usb_interface")) is Route.UNKNOWN
    assert not Route.UNKNOWN.closed


# --- every case in the rule ----------------------------------------------------------------


def test_a_wired_headphone_jack_is_closed() -> None:
    assert route_of(wired_jack("headphones")) is Route.HEADPHONES
    assert route_of(wired_jack("headset")) is Route.HEADPHONES
    assert Route.HEADPHONES.closed


@pytest.mark.parametrize("available", ["no", "unknown"])
def test_a_jack_nothing_is_known_to_be_plugged_into_is_not_closed(available: str) -> None:
    assert route_of(wired_jack("headphones", available)) is Route.UNKNOWN


def test_built_in_speakers_are_never_closed() -> None:
    assert route_of(wired_jack("speaker")) is Route.SPEAKERS
    assert not Route.SPEAKERS.closed


def test_a_line_out_is_unknown() -> None:
    assert route_of(wired_jack("line")) is Route.UNKNOWN


def test_displayport_is_never_closed() -> None:
    """ACP reports DisplayPort audio as an hdmi port on an hdmi profile."""
    objects = wired_jack("hdmi")
    node(objects)["device.profile.name"] = "hdmi-stereo"
    assert route_of(objects) is Route.HDMI


def test_an_hdmi_profile_is_never_closed_whatever_the_port_says() -> None:
    objects = wired_jack("headphones")
    node(objects)["device.profile.name"] = "hdmi-stereo-extra1"
    assert route_of(objects) is Route.HDMI


@pytest.mark.parametrize("form_factor", ["headset", "headphone"])
def test_a_usb_headset_is_closed(form_factor: str) -> None:
    objects = dump("usb_interface")
    device(objects)["info"]["props"]["device.form-factor"] = form_factor
    assert route_of(objects) is Route.USB_HEADSET
    assert Route.USB_HEADSET.closed


def test_a_usb_speaker_is_never_closed() -> None:
    objects = dump("usb_interface")
    device(objects)["info"]["props"]["device.form-factor"] = "speaker"
    assert route_of(objects) is Route.SPEAKERS


def test_usb_headphones_on_a_headphone_port_are_closed() -> None:
    objects = dump("usb_interface")
    set_port(objects, "headphones")
    assert route_of(objects) is Route.USB_HEADSET


@pytest.mark.parametrize(
    "profile", ["a2dp-sink-aac", "headset-head-unit", "headset-head-unit-msbc"]
)
def test_bluetooth_a2dp_and_hfp_headsets_are_closed(profile: str) -> None:
    objects = dump("bluetooth_a2dp")
    node(objects)["api.bluez5.profile"] = profile
    assert route_of(objects) is Route.BLUETOOTH


def test_bluetooth_headphones_are_closed() -> None:
    objects = dump("bluetooth_a2dp")
    device(objects)["info"]["props"]["device.form-factor"] = "headphone"
    assert route_of(objects) is Route.BLUETOOTH


def test_a_bluetooth_device_that_says_speaker_is_never_closed() -> None:
    objects = dump("bluetooth_a2dp")
    device(objects)["info"]["props"]["device.form-factor"] = "speaker"
    assert route_of(objects) is Route.SPEAKERS


@pytest.mark.parametrize("form_factor", ["car", "hands-free", "portable", "hifi", None])
def test_other_bluetooth_devices_are_unknown(form_factor: str | None) -> None:
    objects = dump("bluetooth_a2dp")
    props = device(objects)["info"]["props"]
    if form_factor is None:
        del props["device.form-factor"]
    else:
        props["device.form-factor"] = form_factor
    assert route_of(objects) is Route.UNKNOWN


@pytest.mark.parametrize("profile", ["bap-sink", "off", ""])
def test_a_bluetooth_profile_outside_a2dp_and_hfp_is_unknown(profile: str) -> None:
    objects = dump("bluetooth_a2dp")
    node(objects)["api.bluez5.profile"] = profile
    assert route_of(objects) is Route.UNKNOWN


# --- failing closed ------------------------------------------------------------------------


def test_a_virtual_sink_is_unknown() -> None:
    """A filter, loopback or null sink names no device, so nothing says where it ends up."""
    objects = dump("bluetooth_a2dp")
    for key in ("device.api", "device.id"):
        del node(objects)[key]
    objects = objects[:2]
    assert route_of(objects) is Route.UNKNOWN


def test_no_default_sink_is_unknown() -> None:
    objects = dump("hdmi")
    objects[0]["metadata"] = []
    assert default_sink(objects) is None
    assert route_of(objects) is Route.UNKNOWN


def test_a_default_naming_no_node_is_unknown() -> None:
    objects = dump("bluetooth_a2dp")
    objects[0]["metadata"][0]["value"]["name"] = "gone"
    assert route_of(objects) is Route.UNKNOWN


def test_a_missing_route_is_unknown() -> None:
    objects = wired_jack("headphones")
    device(objects)["info"]["params"]["Route"] = []
    assert route_of(objects) is Route.UNKNOWN


def test_a_missing_device_is_unknown() -> None:
    objects = wired_jack("headphones")
    objects.remove(device(objects))
    assert route_of(objects) is Route.UNKNOWN


@pytest.mark.parametrize("junk", [None, {}, "x", [1, "two", None], [{"type": 3}]])
def test_junk_is_unknown(junk: object) -> None:
    assert route_of(junk) is Route.UNKNOWN


def test_unknown_values_are_unknown() -> None:
    assert classify(Sink("x", None, None, None, None, None, None)) is Route.UNKNOWN
    assert classify(Sink("x", "jack", "pci", "headset", None, "headphones", "yes")) is (
        Route.UNKNOWN
    )


# --- reading it ----------------------------------------------------------------------------


async def test_read_route_runs_pw_dump() -> None:
    raw = (FIXTURES / "bluetooth_a2dp.json").read_bytes()

    async def run(*argv: str) -> bytes:
        assert argv == ("pw-dump",)
        return raw

    route, sink = await read_route(run)
    assert route is Route.BLUETOOTH
    assert sink is not None and sink.name == "bluez_output.00_00_00_00_00_00.1"


@pytest.mark.parametrize("output", [b"", b"not json", b"[]"])
async def test_read_route_fails_closed(output: bytes) -> None:
    async def run(*argv: str) -> bytes:
        return output

    assert await read_route(run) == (Route.UNKNOWN, None)


async def test_read_route_fails_closed_without_pw_dump() -> None:
    async def run(*argv: str) -> bytes:
        raise OSError("pw-dump not found")

    assert await read_route(run) == (Route.UNKNOWN, None)
