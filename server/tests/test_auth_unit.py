import uuid

from jarvis_live.auth import TicketStore, hash_token


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_ticket_is_single_use() -> None:
    store = TicketStore(60, Clock())
    sid, did = uuid.uuid4(), uuid.uuid4()
    t = store.issue(sid, did, "viewer")
    ticket = store.redeem(t, sid)
    assert ticket is not None and ticket.role == "viewer" and ticket.device_id == did
    assert store.redeem(t, sid) is None


def test_ticket_expires_after_60s() -> None:
    clock = Clock()
    store = TicketStore(60, clock)
    sid = uuid.uuid4()
    t = store.issue(sid, uuid.uuid4(), "producer")
    clock.now += 59.9
    assert store.redeem(t, sid) is not None
    t = store.issue(sid, uuid.uuid4(), "producer")
    clock.now += 60
    assert store.redeem(t, sid) is None


def test_ticket_is_bound_to_its_session_and_burned_by_a_mismatch() -> None:
    store = TicketStore(60, Clock())
    sid = uuid.uuid4()
    t = store.issue(sid, uuid.uuid4(), "producer")
    assert store.redeem(t, uuid.uuid4()) is None
    assert store.redeem(t, sid) is None


def test_unknown_ticket() -> None:
    assert TicketStore(60, Clock()).redeem("nope", uuid.uuid4()) is None


def test_hash_token_is_sha256_hex() -> None:
    assert hash_token("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
