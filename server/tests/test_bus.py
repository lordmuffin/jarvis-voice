from jarvis_live.bus import Bus
from jarvis_live.protocol import Ack, SpeakerUpdate


def _update() -> SpeakerUpdate:
    return SpeakerUpdate(speakers=[], assignments=[])


async def test_speaker_update_reaches_only_subscribers_that_opted_in() -> None:
    bus = Bus()
    plain = bus.subscribe("s")
    opted = bus.subscribe("s", features=frozenset({"speaker_update"}))
    bus.publish("s", _update())
    bus.publish("s", Ack(channel="mic", seq=1))
    assert isinstance(await opted.get(), SpeakerUpdate)
    assert isinstance(await opted.get(), Ack)
    assert isinstance(await plain.get(), Ack)  # skipped the update, not the ack


async def test_direct_put_is_not_filtered() -> None:
    sub = Bus().subscribe("s")
    assert sub.put(Ack(channel="mic", seq=1))
