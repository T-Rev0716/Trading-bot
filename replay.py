"""Replay a recorded tape through the strategy and the rules-only baseline. Deterministic:
the same tape and the same thresholds give the same report, every time.

    python replay.py paper_tape.jsonl

The tape is written by the paper shift (main.py): every cycle's candidates with their
recorded judge answers, the pick response, and every quote the engines used. Retune
thresholds.py and replay to see what the change would have done, without a judge call.

Limits: a candidate whose answers were never recorded cannot be judged in replay, and a
token that was never quoted cannot be entered or marked.
"""
import itertools
import json
import sys
from itertools import groupby

from cycle import enter
from market import Quote, ReplayMarket
from paper import build
from pick import PickUnavailable
from report import compare, metrics


def load(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def recorded_judge(cycle_event: dict):
    def judge(question_set, state):
        rec = cycle_event.get("pick")
        labels = [c["label"] for c in state["candidates"]]
        if question_set != "pick" or not rec or rec["labels"] != labels:
            raise PickUnavailable("this candidate set was not judged in the recording")
        return rec["response"]
    return judge


def run(events: list[dict], *, faults=None) -> dict:
    now = [0.0]
    ids = itertools.count(1)
    market = ReplayMarket()
    engines = build(":memory:", market, clock=lambda: now[0], faults=faults,
                    new_id=lambda: f"id{next(ids):06d}")
    log = []
    order = {"quote": 0, "cycle": 1}
    ordered = sorted(enumerate(events), key=lambda ie: (ie[1]["ts"], order[ie[1]["type"]], ie[0]))
    for ts, group in groupby((e for _, e in ordered), key=lambda e: e["ts"]):
        now[0] = ts
        group = list(group)
        for e in group:
            if e["type"] == "quote":
                market.update(e["token_key"], Quote(**e["quote"]))
        for eng in engines.values():
            eng.tick()
        for e in group:
            if e["type"] == "cycle":
                cands = [(c["d"], c.get("answers")) for c in e["candidates"]]
                log.append({"ts": ts, **enter(cands, recorded_judge(e), engines,
                                              e["cycle_id"])})
    return {"metrics": [metrics(e.ledger) for e in engines.values()], "log": log}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    result = run(load(sys.argv[1]))
    print(compare(result["metrics"]))
