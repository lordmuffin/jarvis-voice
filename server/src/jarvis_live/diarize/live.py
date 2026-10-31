"""Provisional speakers while a session is still running.

Each transcribed segment gets a voice embedding; it is matched against the user's enrolled voices,
else against the voices heard so far in this channel (nearest centroid), else it starts a new
voice. Cheap and online, so it can be wrong: the authoritative pass after the session
(``service.py``) replaces all of it."""

from dataclasses import dataclass

from jarvis_live.diarize.identify import Profile, cosine, match, merge


@dataclass(frozen=True)
class ProfileRef:
    id: str
    label: str
    is_owner: bool
    model_id: str
    vector: list[float]


@dataclass
class LiveVoice:
    id: str  # ``live_<channel>_<n>``: never reused by the authoritative pass's ids
    channel: str
    model_id: str
    embedding: list[float]
    n: int = 1
    label: str | None = None
    profile_id: str | None = None
    is_owner: bool = False


class LiveSpeakers:
    def __init__(
        self,
        profiles: list[ProfileRef],
        *,
        cluster_threshold: float,
        match_threshold: float,
        match_margin: float,
    ) -> None:
        self._profiles = profiles
        self._cluster_threshold = cluster_threshold
        self._match_threshold = match_threshold
        self._match_margin = match_margin
        self._voices: dict[str, list[LiveVoice]] = {}  # channel -> voices, in order heard
        self._by_profile: dict[tuple[str, str], LiveVoice] = {}  # (channel, profile id)

    def assign(
        self, channel: str, vector: list[float], model_id: str
    ) -> tuple[LiveVoice, bool] | None:
        """The voice that spoke, and whether it is new. ``None`` for an empty embedding."""
        if not any(vector):
            return None
        voices = self._voices.setdefault(channel, [])
        known = [Profile(p.id, p.vector) for p in self._profiles if p.model_id == model_id]
        if hit := match(
            {"x": vector}, known, threshold=self._match_threshold, margin=self._match_margin
        ).get("x"):
            if (voice := self._by_profile.get((channel, hit))) is not None:
                return voice, False
            ref = next(p for p in self._profiles if p.id == hit)
            voice = LiveVoice(
                f"live_{channel}_{len(voices)}",
                channel,
                model_id,
                list(vector),
                label=ref.label,
                profile_id=ref.id,
                is_owner=ref.is_owner,
            )
            voices.append(voice)
            self._by_profile[(channel, hit)] = voice
            return voice, True
        best = max(
            (
                (cosine(vector, v.embedding), v)
                for v in voices
                if v.profile_id is None
                and v.model_id == model_id
                and len(v.embedding) == len(vector)
            ),
            key=lambda sv: sv[0],
            default=None,
        )
        if best is not None and best[0] >= self._cluster_threshold:
            voice = best[1]
            voice.embedding = merge(voice.embedding, voice.n, vector, 1)
            voice.n += 1
            return voice, False
        voice = LiveVoice(f"live_{channel}_{len(voices)}", channel, model_id, list(vector))
        voices.append(voice)
        return voice, True
