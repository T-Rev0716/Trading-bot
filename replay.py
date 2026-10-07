"""Exact replay of a recorded paper run.

    python replay.py runs/<run_id>/journal.jsonl

Rebuilds empty ledgers, then walks the journal in sequence order. Polls and cycles run
the same code as the live run (session.py); every quote attempt, broker submit and
lookup, scan result and pick is answered with the next recorded answer, and every
decision and equity mark is checked against the record. Nothing is looked up by time,
so no decision can see a quote taken after it. The first difference raises
ReplayDivergence with the sequence number where it happened.

This is a fidelity check, not a what-if tool: a changed threshold is refused up front.
"""
import json
import sys

import session
from journal import ReplayDivergence, ReplayJournal, plain
from judge_client import JudgeMalformed
from paper import build
from report import compare, metrics


def load(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _check_config(ev: dict):
    want, have = ev["config"], plain(session.config_snapshot())
    if want != have:
        diff = sorted(k for k in set(want) | set(have) if want.get(k) != have.get(k))
        raise ReplayDivergence(f"seq {ev['seq']}: thresholds.py differs from the recording "
                               f"in {diff}. Replay is exact; restore them to replay.")
    if ev["version"] != session.JOURNAL_VERSION:
        raise ReplayDivergence(f"journal version {ev['version']}, replay knows "
                               f"{session.JOURNAL_VERSION}")


def _no_live_call(*a, **k):
    raise ReplayDivergence("replay tried to reach a live service")


def run(events: list[dict], db_path: str = ":memory:") -> dict:
    rj = ReplayJournal(events)
    head = rj.peek()
    if not head or head["kind"] != "session":
        raise ReplayDivergence("journal does not start with a session event")
    _check_config(head)
    clock = session.SessionClock(head["t0"])
    engines = build(db_path, None, clock=clock, names=list(head["ledgers"]), journal=rj,
                    starting_cash=head["ledgers"], replay=True)
    session.start(engines, rj, clock)

    while not rj.done():
        ev = rj.peek()
        if ev["kind"] == "poll":
            session.poll(engines, rj, clock, ev["ts"])
        elif ev["kind"] == "cycle":
            try:
                session.cycle(engines, rj, clock, ev["ts"], _no_live_call, _no_live_call)
            except JudgeMalformed:
                pass                     # the recorded run stopped here too
        elif ev["kind"] == "resume":
            _check_config(ev)
            clock.set(ev["t0"])
            session.start(engines, rj, clock, resumed=True)
        else:
            raise ReplayDivergence(f"seq {ev['seq']}: {ev['kind']} outside a poll or cycle")
    return {"engines": engines, "metrics": [metrics(e.ledger) for e in engines.values()]}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    result = run(load(sys.argv[1]))
    print(f"replayed {len(load(sys.argv[1]))} events with no divergence\n")
    print(compare(result["metrics"]))
