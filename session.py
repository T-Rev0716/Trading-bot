"""The desk's two operations, poll and cycle, written once and used by both the paper
shift (main.py) and replay (replay.py). The only difference between the two runs is the
journal: recording in one, answering from the record in the other.

Time is frozen per operation: everything inside one poll or one cycle sees the
operation's timestamp, which is journaled with it.
"""
import time

import cycle as cycle_mod
from judge_client import JudgeMalformed
from thresholds import (BASELINE, EXITS, HARD, MISSING_DATA, PAPER, PICK_MIN_CONF,
                        PICK_MIN_WORTH, RECONCILE, SHAPE_MIN_CROWD, SIZING, SOFT)

JOURNAL_VERSION = 1


class SessionClock:
    def __init__(self, t: float | None = None):
        self.t = time.time() if t is None else t

    def set(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def config_snapshot() -> dict:
    """Every setting that changes a decision. Replay refuses a different one."""
    return {"HARD": HARD, "SOFT": SOFT, "SHAPE_MIN_CROWD": SHAPE_MIN_CROWD,
            "PICK_MIN_WORTH": PICK_MIN_WORTH, "PICK_MIN_CONF": PICK_MIN_CONF,
            "MISSING_DATA": MISSING_DATA, "BASELINE": BASELINE, "PAPER": PAPER,
            "SIZING": SIZING, "EXITS": EXITS, "RECONCILE": RECONCILE}


def start(engines, journal, clock: SessionClock, resumed: bool = False):
    """The first event of a run, and of every resume: what replay must rebuild."""
    journal.emit("resume" if resumed else "session", version=JOURNAL_VERSION, t0=clock(),
                 config=config_snapshot(),
                 ledgers={n: e.ledger.starting_cash() for n, e in engines.items()})


def poll(engines, journal, clock: SessionClock, ts: float) -> dict:
    """Reconcile, work exits and mark equity, ledger by ledger, in a fixed order."""
    clock.set(ts)
    journal.emit("poll", ts=ts)
    out = {}
    for name, e in engines.items():
        r = e.tick()
        journal.emit("reconciled", ledger=name, results=r["reconciled"])
        journal.emit("exits", ledger=name, results=r["exits"])
        journal.emit("equity", ledger=name, cash=e.ledger.cash(),
                     reserved=e.ledger.reserved(), equity=e.ledger.equity())
        out[name] = r
    return out


def cycle(engines, journal, clock: SessionClock, ts: float, scan_fn, judge) -> dict:
    """One scan and its entries.

    scan_fn(ask_judge, free_cash) -> (candidates, stats) runs the funnel; its result is
    journaled as one answer, so replay never runs the scan. The pick goes through the
    journal too. A failure stands the cycle down the same way in both runs; a 422 from
    the judge is re-raised after it is recorded, because every token would hit it."""
    clock.set(ts)
    cycle_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(ts))
    blocked = {n: e.can_enter() for n, e in engines.items()}
    journal.emit("cycle", ts=ts, cycle_id=cycle_id, blocked=blocked)
    if all(blocked.values()):
        return {"cycle_id": cycle_id, "skipped": blocked}

    def journaled_judge(qs, state):
        labels = [c["label"] for c in state.get("candidates", [])]
        return journal.io("judge", qs, lambda: judge(qs, state), request={"labels": labels})

    free = engines["strategy"].ledger.free_cash() if "strategy" in engines else 0.0
    ask = blocked.get("strategy", "absent") is None
    try:
        cands, stats = journal.io("candidates", cycle_id, lambda: scan_fn(ask, free),
                                  request={"ask_judge": ask, "free_cash": free})
        res = cycle_mod.enter(cands, journaled_judge, engines, cycle_id)
    except Exception as e:
        journal.emit("cycle_error", cycle_id=cycle_id, error=type(e).__name__,
                     message=str(e))
        if isinstance(e, JudgeMalformed):
            raise
        return {"cycle_id": cycle_id, "error": f"{type(e).__name__}: {e}"}
    res.pop("strategy_pick", None)
    journal.emit("entries", cycle_id=cycle_id, results=res)
    return {"cycle_id": cycle_id, "blocked": blocked, "stats": stats, **res}
