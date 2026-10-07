"""THE SHIFT, on paper. Live execution is disabled: there is no path to a real venue.

    python main.py            run the paper desk: poll every 5 min, scan every 15
    python main.py --once     one poll and one scan, then exit

Two ledgers trade side by side on the same candidates and the same simulated venue:
  strategy   Jev's answers, SOFT gates, the pick
  baseline   the same checks and rules only, no judge
`python report.py` compares them. `python replay.py paper_tape.jsonl` replays the tape.
"""
import argparse
import json
import logging
import os
import sys
import time

import book
import eligibility
from collect import universe, shortlist, trade_counts, dossier, social_state, GT_PER_MINUTE
from cycle import enter
from filter import free_kill, trade_kill
from questions import STATE_FIELDS
from sizing import intended_ticket

TICK_SECONDS  = 300         # RISK polls every 5 minutes
SCAN_EVERY    = 3           # a scan every third poll: every 15 minutes
NETS          = ("solana", "bsc", "robinhood")
PAGES         = 2           # 3 chains x 2 pages = 6 GT slots before the funnel starts
GT_DOSSIER    = GT_PER_MINUTE - len(NETS) * PAGES     # what is left for dossiers: 4 at 10/min
DEX_BUDGET    = 25          # DexScreener calls per cycle, pass two only
TAPE_FIELDS   = ("x_account", "description", "gt_score_details")   # big; kept as presence
log = logging.getLogger("desk")


def state_for(question_set: str, d: dict) -> dict:
    """Only the fields that set's questions read. Nulls stay null."""
    return {k: d.get(k) for k in STATE_FIELDS[question_set]}


def _count(stats, stage, reason):
    stats[stage][reason] = stats[stage].get(reason, 0) + 1


def scan(fomo, judge, desk, free_cash: float, ask_judge: bool):
    """The funnel. Returns ([(dossier, answers or None)], stats).

    Facts and data rules bench for everyone. Judge rejections bench for the strategy only,
    so the baseline is never starved by a judgement it does not use. Judge failures
    propagate: a desk with no judge stands down, it does not guess."""
    stats = {"seen": 0, "benched": 0, "free": {}, "trade": {}, "chain": {}, "soft": {},
             "candidates": 0}
    cands = []
    gt_slots, dex_slots = GT_DOSSIER, DEX_BUDGET

    for t in shortlist(fomo, universe(NETS, PAGES)):
        stats["seen"] += 1
        scope = book.benched(t["tid"])
        if scope == "shared":
            stats["benched"] += 1
            continue
        if (k := free_kill(t)):
            book.sit(t["tid"], k)
            _count(stats, "free", k)
            continue
        if dex_slots <= 0 or gt_slots <= 0:
            break                                # out of budget, not out of ideas

        t |= trade_counts(t)
        dex_slots -= 1
        if (k := trade_kill(t)):
            book.sit(t["tid"], k)
            _count(stats, "trade", k)
            continue

        gt_slots -= 1                            # a failed call still costs the slot
        try:
            d = dossier(t)
        except Exception as e:
            log.warning("dossier failed %s: %s", t["token_key"], e)
            book.sit(t["tid"], "dossier_failed")
            continue
        d["x_account"] = desk.read_x(d["x_handle"]) if d["x_handle"] else None

        v = eligibility.check(d)                 # facts + missing-data policy, both ledgers
        if not v.ok:
            book.sit(t["tid"], v.reason)
            _count(stats, "chain", v.reason)
            continue

        ans = None
        if ask_judge and scope != "strategy":
            d["intended_ticket_usd"] = intended_ticket(free_cash)
            ans = dict(judge("market", state_for("market", d))["answers"])
            cs = eligibility.CHAIN_SET[d["chain"]]
            ans |= judge(cs, state_for(cs, d))["answers"]
            if d["x_account"]:
                ans |= judge("social", social_state(d))["answers"]
            sv = eligibility.check(d, ans)
            if not sv.ok:
                book.sit(t["tid"], sv.reason, scope="strategy")
                _count(stats, "soft", sv.reason)
        cands.append((d, ans))

    stats["candidates"] = len(cands)
    return cands, stats


def _tape_row(d: dict) -> dict:
    row = {k: v for k, v in d.items() if k not in TAPE_FIELDS}
    row["x_account"] = {"present": True} if d.get("x_account") else None
    return row


def run_cycle(fomo, judge, desk, engines, tape_path=None, now=None):
    """One scan and its entries. Skipped entirely when neither ledger can enter."""
    blocked = {n: e.can_enter() for n, e in engines.items()}
    if all(blocked.values()):
        return {"skipped": blocked}
    now = now if now is not None else time.time()
    cycle_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    fomo.token()                                 # Privy bearer lives ~60 min, refresh it
    strat = engines["strategy"]
    cands, stats = scan(fomo, judge, desk, strat.ledger.free_cash(),
                        ask_judge=blocked["strategy"] is None)
    res = enter(cands, judge, engines, cycle_id)
    if tape_path:
        with open(tape_path, "a") as f:
            f.write(json.dumps({"type": "cycle", "ts": now, "cycle_id": cycle_id,
                                "candidates": [{"d": _tape_row(d), "answers": a}
                                               for d, a in cands],
                                "pick": res.get("strategy_pick")}, default=str) + "\n")
    res.pop("strategy_pick", None)
    return {"cycle_id": cycle_id, "blocked": blocked, "stats": stats, **res}


def main(fomo, judge, desk, engines, tape_path=None, once=False):
    from judge_client import JudgeMalformed
    tick = 0
    while True:
        started = time.monotonic()
        polls = {n: e.tick() for n, e in engines.items()}
        for n, p in polls.items():
            if p["reconciled"] or p["exits"]:
                desk.report(f"{n}: {p}")
        if tick % SCAN_EVERY == 0:
            try:
                desk.report(run_cycle(fomo, judge, desk, engines, tape_path))
            except JudgeMalformed as e:
                log.error("422 from the judge, fix questions.py before the next run: %s", e)
                desk.report(f"stood down, malformed question: {e}")
                raise                            # every token would hit the same wall
            except Exception as e:
                log.exception("cycle blew up: %s", e)
                desk.report(f"stood down: {type(e).__name__}: {e}")
        if once:
            return
        tick += 1
        time.sleep(max(0, TICK_SECONDS - (time.monotonic() - started)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="one poll and one scan, then exit")
    ap.add_argument("--live", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.live:
        sys.exit("Live execution is disabled. This build trades on paper only.")

    from desk import Desk
    from fomo_api import Fomo
    from judge_client import judge
    from market import DexScreenerMarket, RecordingMarket
    from paper import build

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    tape = os.environ.get("PAPER_TAPE", "paper_tape.jsonl")
    market = RecordingMarket(DexScreenerMarket(time.time), tape)
    engines = build(os.environ.get("PAPER_DB", "paper.db"), market)
    main(Fomo(), judge, Desk(), engines, tape_path=tape, once=args.once)
