"""The journal: one ordered record of everything that happened, and the means to replay it.

Every event has a sequence number. Two kinds:

    io      an answer from outside: a quote attempt, a broker submit or lookup, the
            scan's candidates, the judge's pick. Recorded as the value or the exception.
    emit    what the desk did with it: polls, cycles, reconciliations, entries, exits,
            equity marks. Recorded as data.

RecordJournal writes events as they happen. ReplayJournal walks the same events in the
same order: an io call is answered with the next recorded answer, and an emit is
compared with the next recorded event. There is no lookup by time or by key, so replay
cannot see a quote before the moment it was taken, and any drift (a different call, a
different key, a different request, a different decision) raises ReplayDivergence at
the first event where it happens.
"""
import json
from dataclasses import asdict

import broker
import judge_client
import market
import pick
from broker import Fill


class ReplayDivergence(BaseException):
    """Not an Exception on purpose: the engine treats any Exception from the broker as a
       lost acknowledgement, and a divergence must never be absorbed that way."""


class RecordedError(RuntimeError):
    """An exception type the journal does not know, kept by name and message."""


# exceptions that cross the io boundary, rebuilt by name on the way out
_ERRORS = {c.__name__: c for c in (
    broker.BrokerReject, broker.DeliveryTimeout, broker.LookupUnavailable,
    market.QuoteUnavailable, judge_client.JudgeMalformed, judge_client.JudgeUnavailable,
    pick.PickUnavailable)}


def plain(x):
    """The JSON form, exactly as it will be read back. Python floats round-trip exactly."""
    return json.loads(json.dumps(x, default=_default))


def _default(x):
    if isinstance(x, (Fill, market.Quote)):
        return asdict(x)
    if isinstance(x, (set, frozenset)):
        return sorted(x)
    raise TypeError(f"cannot journal {type(x).__name__}")


# per-kind codecs: value -> JSON, JSON -> value
def _enc_lookup(v):
    status, x = v
    return {"status": status, "fill": asdict(x) if isinstance(x, Fill) else None,
            "reason": x if not isinstance(x, Fill) else None}


def _dec_lookup(j):
    if j["fill"]:
        return j["status"], _fill(j["fill"])
    return j["status"], j["reason"]


def _fill(j):
    return Fill(**{**j, "flags": tuple(j["flags"])})


CODECS = {
    "quote": (asdict, lambda j: market.Quote(**j)),
    "submit": (asdict, _fill),
    "lookup": (_enc_lookup, _dec_lookup),
    "candidates": (lambda v: {"candidates": [[d, a] for d, a in v[0]], "stats": v[1]},
                   lambda j: ([(d, a) for d, a in j["candidates"]], j["stats"])),
    "judge": (lambda v: v, lambda j: j),
}


def _error(j):
    cls = _ERRORS.get(j["error"])
    return cls(j["message"]) if cls else RecordedError(f"{j['error']}: {j['message']}")


class RecordJournal:
    def __init__(self, path: str | None = None):
        """Appends to an existing journal file, continuing its sequence (a resume)."""
        self.path, self.events, self.seq = path, [], 0
        if path:
            try:
                with open(path) as f:
                    self.seq = sum(1 for line in f if line.strip())
            except FileNotFoundError:
                pass

    def _write(self, ev: dict):
        self.seq += 1
        ev = {"seq": self.seq, **ev}
        self.events.append(ev)
        if self.path:
            with open(self.path, "a") as f:
                f.write(json.dumps(ev) + "\n")

    def emit(self, kind: str, **data):
        self._write({"kind": kind, **plain(data)})

    def io(self, kind: str, key, fn, request=None):
        """Call fn once, record what came back, and hand back exactly what was recorded,
           so the live run sees the same value replay will."""
        enc, dec = CODECS[kind]
        base = {"kind": kind, "key": plain(key), "request": plain(request)}
        try:
            value = fn()
        except Exception as e:
            j = {"error": type(e).__name__, "message": str(e)}
            self._write({**base, **j})
            raise _error(j) from e
        j = plain(enc(value))
        self._write({**base, "value": j})
        return dec(j)


class ReplayJournal:
    def __init__(self, events: list[dict]):
        self.events = sorted(events, key=lambda e: e["seq"])
        seqs = [e["seq"] for e in self.events]
        if seqs != list(range(1, len(seqs) + 1)):
            raise ReplayDivergence("journal sequence has gaps or repeats")
        self.pos = 0

    def _next(self, kind: str) -> dict:
        if self.pos >= len(self.events):
            raise ReplayDivergence(f"replay wanted {kind} after the last recorded event")
        ev = self.events[self.pos]
        self.pos += 1
        if ev["kind"] != kind:
            raise ReplayDivergence(f"seq {ev['seq']}: recorded {ev['kind']}, replay did {kind}")
        return ev

    def peek(self) -> dict | None:
        return self.events[self.pos] if self.pos < len(self.events) else None

    def done(self) -> bool:
        return self.pos == len(self.events)

    def emit(self, kind: str, **data):
        ev = self._next(kind)
        got = plain(data)
        want = {k: v for k, v in ev.items() if k not in ("seq", "kind")}
        if got != want:
            raise ReplayDivergence(f"seq {ev['seq']} {kind}: recorded {want}, replay {got}")

    def io(self, kind: str, key, fn=None, request=None):
        ev = self._next(kind)
        if ev["key"] != plain(key):
            raise ReplayDivergence(f"seq {ev['seq']} {kind}: recorded key {ev['key']}, "
                                   f"replay asked for {plain(key)}")
        if ev["request"] != plain(request):
            raise ReplayDivergence(f"seq {ev['seq']} {kind}: recorded request "
                                   f"{ev['request']}, replay sent {plain(request)}")
        if "error" in ev:
            raise _error(ev)
        return CODECS[kind][1](ev["value"])


class NullJournal:
    """No recording. For tests and one-off tools that do not need a replayable run."""

    def emit(self, kind, **data):
        pass

    def io(self, kind, key, fn, request=None):
        return fn()


class JournaledMarket:
    """Every quote attempt, success or failure, goes through the journal."""

    def __init__(self, inner, journal):
        self.inner, self.journal = inner, journal

    def quote(self, key: str):
        return self.journal.io("quote", key, lambda: self.inner.quote(key))


class JournaledBroker:
    """Records the PaperVenue's answers; in replay, answers from the record instead.
       Executes nothing itself."""

    def __init__(self, inner, journal):
        self.inner, self.journal = inner, journal

    def submit(self, order: dict, ref_price, liquidity_usd, flags=()):
        request = {"side": order["side"], "notional_usd": order["notional_usd"],
                   "qty": order["qty"], "ref_price": ref_price,
                   "liquidity_usd": liquidity_usd, "flags": list(flags)}
        return self.journal.io("submit", order["order_id"],
                               lambda: self.inner.submit(order, ref_price, liquidity_usd,
                                                         flags), request)

    def lookup(self, order_id: str):
        return self.journal.io("lookup", order_id, lambda: self.inner.lookup(order_id))
