"""THE SHIFT, on paper. Live execution is disabled: there is no path to a real venue.

    python main.py                           a new run in runs/<run_id>/
    python main.py --starting-cash 1500      the paper bank for a new run
    python main.py --resume runs/<run_id>    continue a run, e.g. after a crash
    python main.py --once                    one poll and one scan, then exit
    python main.py --nets solana --max-polls 12
                                             a bounded run on Solana only (an hour)

Each run is a directory: paper.db (ledgers) and journal.jsonl (every event in order).
`python report.py runs/<run_id>` compares the ledgers; `python replay.py
runs/<run_id>/journal.jsonl` replays the journal and checks it reproduces the run.

Two ledgers trade side by side on the same candidates and the same simulated venue:
  strategy   Jev's answers, SOFT gates, the pick
  baseline   the same checks and rules only, no judge
"""
import argparse
import logging
import os
import sys
import time

import book
import eligibility
import session
from collect import universe, shortlist, trade_counts, dossier, social_state, GT_PER_MINUTE
from filter import free_kill, trade_kill
from questions import STATE_FIELDS
from sizing import intended_ticket

TICK_SECONDS  = 300         # RISK polls every 5 minutes
SCAN_EVERY    = 3           # a scan every third poll: every 15 minutes
NETS          = ("solana", "bsc", "robinhood")
PAGES         = 2           # 3 chains x 2 pages = 6 GT slots before the funnel starts
GT_DOSSIER    = GT_PER_MINUTE - len(NETS) * PAGES     # what is left for dossiers: 4 at 10/min
DEX_BUDGET    = 25          # DexScreener calls per cycle, pass two only
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


def main(fomo, judge, desk, engines, journal, clock, once=False, max_polls=None):
    from judge_client import JudgeMalformed
    tick = 0
    while True:
        started = time.monotonic()
        polls = session.poll(engines, journal, clock, time.time())
        for n, p in polls.items():
            if p["reconciled"] or p["exits"]:
                desk.report(f"{n}: {p}")
        if tick % SCAN_EVERY == 0:
            scan_fn = lambda ask, free: (fomo.token(), scan(fomo, judge, desk, free, ask))[1]
            try:
                desk.report(session.cycle(engines, journal, clock, time.time(), scan_fn,
                                          judge))
            except JudgeMalformed as e:
                log.error("422 from the judge, fix questions.py before the next run: %s", e)
                desk.report(f"stood down, malformed question: {e}")
                raise                            # every token would hit the same wall
        tick += 1
        if once or (max_polls is not None and tick >= max_polls):
            return
        time.sleep(max(0, TICK_SECONDS - (time.monotonic() - started)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="one poll and one scan, then exit")
    ap.add_argument("--starting-cash", type=float,
                    default=os.environ.get("PAPER_STARTING_CASH"),
                    help="paper bank for a new run (default thresholds.PAPER)")
    ap.add_argument("--resume", metavar="RUN_DIR", help="continue an existing run")
    ap.add_argument("--nets", default=",".join(NETS),
                    help="chains to scan, comma separated (default: %(default)s)")
    ap.add_argument("--max-polls", type=int, help="stop after this many polls")
    ap.add_argument("--live", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.live:
        sys.exit("Live execution is disabled. This build trades on paper only.")
    NETS = tuple(n for n in args.nets.split(",") if n)
    GT_DOSSIER = GT_PER_MINUTE - len(NETS) * PAGES

    from desk import Desk
    from fomo_api import Fomo
    from journal import RecordJournal
    from judge_client import judge
    from market import DexScreenerMarket
    from paper import build

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    clock = session.SessionClock()
    run_dir = args.resume or os.path.join(
        "runs", time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(clock())))
    if not args.resume and os.path.exists(run_dir):
        sys.exit(f"{run_dir} already exists")
    os.makedirs(run_dir, exist_ok=True)
    journal = RecordJournal(os.path.join(run_dir, "journal.jsonl"))
    engines = build(os.path.join(run_dir, "paper.db"), DexScreenerMarket(clock), clock=clock,
                    journal=journal, starting_cash=args.starting_cash)
    session.start(engines, journal, clock, resumed=bool(args.resume))
    log.info("paper run %s, live execution disabled", run_dir)
    main(Fomo(), judge, Desk(), engines, journal, clock, once=args.once,
         max_polls=args.max_polls)
