"""Exact replay of a recorded paper run.

    python replay.py runs/<run_id>/journal.jsonl
    python replay.py --compare runs/<run_id>     also compare every ledger row with the
                                                 run's own paper.db

Replay starts from the RECORDED SCANNER OUTPUT. The scan (FOMO, GeckoTerminal,
DexScreener trade counts, Solana RPC, the per-token judge calls and the bench) is not
re-executed: its result is one journaled `candidates` answer per cycle. Replay checks
everything from there on: eligibility, the pick, sizing, every quote attempt, broker
submit and lookup, reconciliation, exits and equity. It cannot detect a scanner or
per-token judging difference.

Rebuilds empty ledgers, then walks the journal in sequence order. Polls and cycles run
the same code as the live run (session.py); every quote attempt, broker submit and
lookup, scan result and pick is answered with the next recorded answer, and every
decision and equity mark is checked against the record. Nothing is looked up by time,
so no decision can see a quote taken after it. The first difference raises
ReplayDivergence with the sequence number where it happened.

This is a fidelity check, not a what-if tool: a changed threshold is refused up front.
"""
import json
import os
import sqlite3
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


LEDGER_TABLES = {"accounts": "ledger", "orders": "ledger, created_at, order_id",
                 "order_events": "rowid", "positions": "ledger, position_id",
                 "marks": "rowid"}


def ledger_rows(db: sqlite3.Connection) -> dict:
    return {t: [tuple(r) for r in db.execute(f"SELECT * FROM {t} ORDER BY {o}")]
            for t, o in LEDGER_TABLES.items()}


def compare_rows(recorded: dict, replayed: dict) -> list[str]:
    """Differences, table by table. Empty means every ledger row matches."""
    diffs = []
    for t in LEDGER_TABLES:
        a, b = recorded[t], replayed[t]
        if len(a) != len(b):
            diffs.append(f"{t}: {len(a)} recorded rows, {len(b)} replayed")
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                diffs.append(f"{t} row {i}: recorded {x} != replayed {y}")
    return diffs


def compare_run(run_dir: str) -> tuple[dict, list[str]]:
    result = run(load(os.path.join(run_dir, "journal.jsonl")))
    db = next(iter(result["engines"].values())).ledger.db
    rec = sqlite3.connect(f"file:{os.path.join(run_dir, 'paper.db')}?mode=ro", uri=True)
    return result, compare_rows(ledger_rows(rec), ledger_rows(db))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--compare"] and len(args) == 2:
        result, diffs = compare_run(args[1])
        rows = ledger_rows(next(iter(result["engines"].values())).ledger.db)
        print("journal replayed with no divergence")
        print("ledger rows: " + ", ".join(f"{t} {len(r)}" for t, r in rows.items()))
        if diffs:
            print("LEDGER ROWS DIFFER:\n" + "\n".join(diffs[:50]))
            sys.exit(1)
        print("every ledger row matches the recorded paper.db\n")
    elif len(args) == 1:
        result = run(load(args[0]))
        print(f"replayed {len(load(args[0]))} events with no divergence\n")
    else:
        print(__doc__)
        sys.exit(2)
    print("SIMULATED PAPER RESULTS. Not market evidence.\n")
    print(compare(result["metrics"]))
