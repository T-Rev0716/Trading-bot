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
from watchlist import Watchlist
from collect import universe, shortlist, trade_counts, dossier, social_state, GT_PER_MINUTE
from filter import age_band, free_kill, trade_kill
from questions import STATE_FIELDS
from sizing import intended_ticket
from thresholds import HARD

TICK_SECONDS  = 300         # RISK polls every 5 minutes
SCAN_EVERY    = 3           # a scan every third poll: every 15 minutes
NETS          = ("solana", "bsc", "robinhood")
PAGES         = 2           # 3 chains x 2 pages = 6 GT slots before the funnel starts
GT_DOSSIER    = GT_PER_MINUTE - len(NETS) * PAGES     # what is left for dossiers: 4 at 10/min
DEX_BUDGET    = 25          # DexScreener calls per cycle, pass two only
WATCH_MAX     = 200         # watched tokens; re-fetching them costs one FOMO call per 20
WATCH         = Watchlist(book.DB, HARD["max_age_hours"], WATCH_MAX, book._lock)
log = logging.getLogger("desk")


def state_for(question_set: str, d: dict) -> dict:
    """Only the fields that set's questions read. Nulls stay null."""
    return {k: d.get(k) for k in STATE_FIELDS[question_set]}


def _count(stats, stage, reason):
    stats[stage][reason] = stats[stage].get(reason, 0) + 1


def _ages(rows) -> dict:
    """Observed ages by band, so a starved cycle says which way it is starved."""
    out = {}
    for band in ("missing", "too_young", "in_range", "too_old"):
        ages = [t["age_minutes"] for t in rows if age_band(t["age_minutes"]) == band]
        out[band] = {"n": len(ages)} if band == "missing" else \
            {"n": len(ages), "min_minutes": round(min(ages), 1) if ages else None,
             "max_minutes": round(max(ages), 1) if ages else None}
    return out


def _span(b) -> str:
    return f"[{b['min_minutes']}-{b['max_minutes']} min]" if b["n"] else ""


def scan(fomo, judge, desk, free_cash: float, ask_judge: bool, now: float | None = None,
         watch: Watchlist | None = None):
    """The funnel. Returns ([(dossier, answers or None)], stats).

    Discovery is the newest pools plus the watchlist, all re-fetched from FOMO now.
    Facts and data rules bench for everyone. Judge rejections bench for the strategy only,
    so the baseline is never starved by a judgement it does not use. A token too young
    is watched, never benched, so it is looked at again once it matures. Judge failures
    propagate: a desk with no judge stands down, it does not guess."""
    now = time.time() if now is None else now
    watch = WATCH if watch is None else watch
    stats = {"seen": 0, "benched": 0, "free": {}, "trade": {}, "chain": {}, "soft": {},
             "deferred_budget": 0, "candidates": 0}
    cands = []
    gt_slots, dex_slots = GT_DOSSIER, DEX_BUDGET

    expired = watch.prune(now)
    discovered = universe(NETS, PAGES)
    watched = watch.active(now)
    fresh = set(discovered)
    ids = list(dict.fromkeys(discovered + [w for w in watched if w not in fresh]))
    rows = shortlist(fomo, ids, now)
    returned = {t["tid"] for t in rows}
    evicted = 0
    stats["discovery"] = {"discovered": len(fresh), "watched": len(watched),
                          "refetched_from_watchlist": len(ids) - len(fresh),
                          "requested": len(ids), "fomo_unknown": len(set(ids) - returned),
                          "expired": expired}
    stats["age"] = _ages(rows)

    def reject(t, reason, stage, scope="shared"):
        book.sit(t["tid"], reason, scope, now)
        _count(stats, stage, reason)
        if scope == "shared" and book.bench_minutes(reason) >= HARD["max_age_hours"] * 60:
            watch.drop(t["tid"])                 # a permanent fact: stop watching

    for t in rows:
        stats["seen"] += 1
        if age_band(t["age_minutes"]) == "too_old":
            watch.drop(t["tid"])
        else:
            evicted += watch.observe(t["tid"], t.get("created_at"), now)
        scope = book.benched(t["tid"], now)
        if scope == "shared":
            stats["benched"] += 1
            continue
        if (k := free_kill(t)):
            if k == "age_too_young":
                _count(stats, "free", k)         # watched, not benched
            else:
                reject(t, k, "free")
            continue
        if dex_slots <= 0 or gt_slots <= 0:
            stats["deferred_budget"] += 1        # still watched: next cycle, not never
            continue

        t |= trade_counts(t)
        dex_slots -= 1
        if (k := trade_kill(t)):
            reject(t, k, "trade")
            continue

        gt_slots -= 1                            # a failed call still costs the slot
        try:
            d = dossier(t)
        except Exception as e:
            log.warning("dossier failed %s: %s", t["token_key"], e)
            reject(t, "dossier_failed", "chain")
            continue
        d["x_account"] = desk.read_x(d["x_handle"]) if d["x_handle"] else None

        v = eligibility.check(d)                 # facts + missing-data policy, both ledgers
        if not v.ok:
            reject(t, v.reason, "chain")
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
                reject(t, sv.reason, "soft", scope="strategy")
        cands.append((d, ans))

    stats["candidates"] = len(cands)
    stats["discovery"] |= {"evicted": evicted, "watchlist_size": watch.size()}
    a = stats["age"]
    log.info("scan: %d discovered + %d re-fetched from watchlist; ages: %d missing, "
             "%d too young %s, %d in range %s, %d too old %s; %d deferred by budget",
             len(fresh), len(ids) - len(fresh), a["missing"]["n"],
             a["too_young"]["n"], _span(a["too_young"]), a["in_range"]["n"],
             _span(a["in_range"]), a["too_old"]["n"], _span(a["too_old"]),
             stats["deferred_budget"])
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
