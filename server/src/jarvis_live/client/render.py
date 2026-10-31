"""Terminal rendering of server events for ``capture tail``."""

from jarvis_live.protocol import Copilot, Error, FinalNote, Segment, ServerMessage


def _mmss(ms: int) -> str:
    m, s = divmod(ms // 1000, 60)
    return f"{m:02d}:{s:02d}"


class TailRenderer:
    """Turns server events into lines. Copilot snapshots are cumulative, so each item is printed
    once, the first time its id is seen."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def render(self, msg: ServerMessage) -> list[str]:
        if isinstance(msg, Segment):
            return [f"[{_mmss(msg.start_ms)}] {msg.speaker}: {msg.text}"]
        if isinstance(msg, Copilot):
            return self._copilot(msg)
        if isinstance(msg, FinalNote):
            return [f"final note: {msg.title} ({msg.path})"]
        if isinstance(msg, Error):
            return [f"error {msg.code}: {msg.message}"]
        return []

    def _new(self, kind: str, item_id: str) -> bool:
        key = f"{kind}:{item_id}"
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    def _copilot(self, msg: Copilot) -> list[str]:
        out: list[str] = []
        for s in msg.suggestions:
            if self._new("s", s.id):
                out.append(f"  ? {s.text}")
        for a in msg.actions:
            if self._new("a", a.id):
                who = f" ({a.owner})" if a.owner else ""
                out.append(f"  [action] {a.text}{who}")
        for d in msg.decisions:
            if self._new("d", d.id):
                out.append(f"  [decision] {d.text}")
        return out
