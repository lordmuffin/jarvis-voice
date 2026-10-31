"""The wire form of a session's speakers."""

from jarvis_live.db.models import SessionSpeaker
from jarvis_live.protocol import SpeakerInfo


def speaker_info(sp: SessionSpeaker) -> SpeakerInfo:
    return SpeakerInfo(
        id=sp.speaker_id,
        label=sp.label,
        enrolled=sp.profile_id is not None,
        is_owner=sp.is_owner,
        ordinal=sp.ordinal,
    )
