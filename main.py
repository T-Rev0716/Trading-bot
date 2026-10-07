"""THE SHIFT. The process that never stops: owns the cycle, calls everything in order,
hands finished orders to the seats.

    python main.py            shadow mode: everything except send the order and take the book
    python main.py --live     after the shadow week
    python main.py --once     one cycle, then exit
"""
import argparse
import logging
import time

import book
from collect import universe, shortlist, trade_counts, dossier, social_state, GT_PER_MINUTE
from filter import free_kill, trade_kill, chain_kill, soft_kill
from pick import pick, order as make_order
from questions import STATE_FIELDS
from thresholds import MAX_TICKET_OF_BANK

CHAIN_SET     = {1399811149: "solana", 56: "bsc", 8453: "bsc", 4663: "robinhood"}
CYCLE_SECONDS = 900
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


def run_once(fomo, judge, desk, bank, shadow=True):
    """Returns (order or None, stats). Judge failures propagate: a desk with no judge
       does not fall back to guessing, it stands down for the cycle."""
    if (h := book.held()):                       # RISK owns the desk right now
        log.info("holding %s for %.0f min, no scan this cycle", h["ticker"], h["minutes"])
        return None, {"held": h["ticker"], "minutes": round(h["minutes"])}

    stats = {"seen": 0, "benched": 0, "free": {}, "trade": {},
             "chain": {}, "soft": {}, "survivors": 0}
    survivors = []
    gt_slots, dex_slots = GT_DOSSIER, DEX_BUDGET

    ids = universe(NETS, PAGES)                  # fresh pools, GT slots spent here
    for t in shortlist(fomo, ids):               # pass one: free, no per-token requests
        stats["seen"] += 1
        if book.benched(t["tid"]):               # already judged, still serving its time
            stats["benched"] += 1
            continue
        if (k := free_kill(t)):
            book.sit(t["tid"], k)
            _count(stats, "free", k)
            continue

        if dex_slots <= 0 or gt_slots <= 0:
            break                                # out of budget, not out of ideas

        t |= trade_counts(t)                     # pass two: one DexScreener call
        dex_slots -= 1
        if (k := trade_kill(t)):
            book.sit(t["tid"], k)
            _count(stats, "trade", k)
            continue

        gt_slots -= 1                            # a failed call still costs the slot
        try:
            d = dossier(t)                       # pass three: one GeckoTerminal slot
        except Exception as e:
            log.warning("dossier failed %s: %s", t["ticker"], e)
            book.sit(t["tid"], "dossier_failed")
            continue                             # missing is missing, not a pass

        if (k := chain_kill(d)):
            book.sit(t["tid"], k)                # facts bench longest
            _count(stats, "chain", k)
            continue

        d["intended_ticket_usd"] = round(bank * MAX_TICKET_OF_BANK, 2)
        d["x_account"] = desk.read_x(d["x_handle"]) if d["x_handle"] else None

        # pass four: one call per set per token. A judge failure ends the cycle.
        market = judge("market", state_for("market", d))
        ans = dict(market["answers"])
        chain_set = CHAIN_SET[d["net"]]
        ans |= judge(chain_set, state_for(chain_set, d))["answers"]
        if d["x_account"]:
            ans |= judge("social", social_state(d))["answers"]

        if (k := soft_kill(ans)):
            book.sit(t["tid"], k)
            _count(stats, "soft", k)
            continue

        survivors.append((d, ans, market["model"]))

    stats["survivors"] = len(survivors)
    log.info("cycle: %(seen)s seen, %(benched)s benched, free %(free)s, trade %(trade)s, "
             "chain %(chain)s, soft %(soft)s, survivors %(survivors)s", stats)

    if not survivors:
        return None, stats
    if len(survivors) == 1:                      # a choice over one option proves nothing
        d, ans, model = survivors[0]
        order = make_order(d, ans, model, confidence=None)
    else:
        order = pick(judge, [(d, a) for d, a, _ in survivors])     # pass five

    if order is None:
        stats["note"] = "pick stood down"
        return None, stats
    order["order_id"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if shadow:
        desk.log_shadow(order, stats)            # written, never sent
        stats["note"] = f"shadow order {order['token']['ticker']}"
        return None, stats

    book.take(order)          # the desk is now held. No scan until RISK calls release().
    return order, stats


def main(fomo, judge, desk, shadow=True, once=False):
    """desk is your Grok Bot side, see desk.py for the five things it provides."""
    from judge_client import JudgeMalformed
    while True:
        started = time.monotonic()
        try:
            fomo.token()                         # Privy bearer lives ~60 min, refresh it
            order, stats = run_once(fomo, judge, desk, desk.bank(), shadow)
            if order:
                try:
                    desk.send_to_seats(order)
                except Exception:
                    book.release()               # nobody got it, so nothing is held
                    raise
            desk.report(order, stats, stats.get("note", ""))
        except JudgeMalformed as e:
            log.error("422 from the judge, fix questions.py before the next run: %s", e)
            desk.report(None, {}, f"stood down, malformed question: {e}")
            raise                                # every token would hit the same wall
        except Exception as e:
            log.exception("cycle blew up: %s", e)
            desk.report(None, {}, f"stood down: {type(e).__name__}: {e}")
        if once:
            return
        time.sleep(max(0, CYCLE_SECONDS - (time.monotonic() - started)))


if __name__ == "__main__":
    from desk import Desk
    from fomo_api import Fomo
    from judge_client import judge

    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="send orders and take the book")
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    main(Fomo(), judge, Desk(), shadow=not args.live, once=args.once)
