"""SYNTHETIC scenarios. Not market evidence.

    python scenarios.py        prints the results and rewrites examples/SYNTHETIC_RESULTS.md
                               and the replayable journals in examples/synthetic/

Every price path, answer and outage here is invented to exercise the machinery: the
strategy-vs-baseline split, the order lifecycle under lost acknowledgements, blind closes
and the zero-recovery stress, and the effect of the starting bank on the fee floor. The
trap scenario is rigged so the judge looks right. None of it says anything about whether
the strategy has an edge in a real market. Only recorded paper runs on live data can.
"""
import json
import os

import session
from ids import token_key
from journal import RecordJournal
from market import Quote, StaticMarket
from paper import build
from report import metrics
from venue import FaultPlan

T0 = 1_700_000_000.0
BANKS = (1_500.0, 10_000.0)


def dossier(addr, ticker, **kw) -> dict:
    d = {"addr": addr, "net": 1399811149, "chain": "solana",
         "token_key": token_key("solana", addr), "ticker": ticker, "tid": f"{addr}:1399811149",
         "age_minutes": 90.0, "mcap_usd": 400_000.0, "liquidity_usd": 100_000.0,
         "volume_h24": 300_000.0, "price_usd": 1.0, "holder_count": 500,
         "change": {"5m": 0.01, "1h": 0.1, "24h": 0.3},
         "buys_h1": 60, "sells_h1": 40, "buys_h6": 300, "sells_h6": 240, "trades_h24": 900,
         "volume_h1": 15_000.0, "volume_h6": 80_000.0, "top_10_share": 0.30,
         "top_wallet_share": 0.02, "mint_authority_open": False,
         "freeze_authority_open": False, "is_honeypot": None,
         "developer_holding_percentage": 1.0, "gt_score_details": {"pool": 50},
         "x_handle": "coin", "x_account": {"handle": "coin"}}
    d.update(kw)
    return d


def answers(shape="crowd") -> dict:
    p = {"crowd": 0.1, "one_buyer": 0.1, "fading": 0.1, "too_early": 0.1}
    p[shape] = 0.7
    noul = lambda v: {"type": "noul", "noul": v}
    return {"shape": {"type": "choice", "choice": shape, "confidence": 0.7, "probabilities": p},
            "liquidity_fits_ticket": noul(0.9), "momentum_already_spent": noul(0.2),
            "concentration_is_exit_risk": noul(0.1),
            "authority_risk": {"type": "choice", "choice": "renounced", "confidence": 0.9,
                               "probabilities": {"renounced": 0.9}},
            "dev_still_loaded": noul(0.1), "account_is_the_project": noul(0.95),
            "audience_is_real": noul(0.8), "recycled_account": noul(0.05),
            "effort": {"type": "score", "score": 2.0}}


GOOD = dossier("GoodMint11111111111111111111111111111111111", "GOOD")
TRAP = dossier("TrapMint11111111111111111111111111111111111", "TRAP",
               buys_h1=85, sells_h1=15, buys_h6=400, sells_h6=100)
RUG = dossier("RugMint111111111111111111111111111111111111", "RUG")


def _quote(price, v6=80_000.0, ts=T0):
    return Quote(price_usd=price, liquidity_usd=100_000.0, volume_h6=v6, volume_h24=300_000.0,
                 ts=ts)


def _paths(paths: dict, n: int, fade_last=True):
    """Per step: {token_key: price or None (outage)}; volume dies on the last step."""
    steps = []
    for i in range(n):
        steps.append({k: (None if p[i] is None else
                          (p[i], 10_000.0 if fade_last and i == n - 1 else 80_000.0))
                      for k, p in paths.items()})
    return steps


SCENARIOS = {
    # the rules prefer TRAP (bigger buy share); the judge calls it one_buyer
    "trap": {
        "candidates": [(GOOD, answers()), (TRAP, answers("one_buyer"))],
        "steps": _paths({GOOD["token_key"]: [1.00, 1.05, 1.12, 1.20, 1.30, 1.38, 1.40, 1.36],
                         TRAP["token_key"]: [1.00, 1.02, 0.90, 0.75, 0.60, 0.50, 0.42, 0.40]},
                        8),
        "faults": None,
    },
    # both ledgers buy RUG; after two polls it stops quoting for good (delisted, or the
    # pool was pulled). RISK closes blind at the haircut; the stress says it was worth $0.
    "unsellable": {
        "candidates": [(RUG, answers())],
        "steps": _paths({RUG["token_key"]: [1.00, 1.10, 0.95, None, None, None]}, 6,
                        fade_last=False),
        "faults": None,
    },
    # the first entry's acknowledgement is lost and the venue cannot be asked twice;
    # the order stays UNKNOWN, blocks that ledger, then reconciles as filled
    "lost_ack": {
        "candidates": [(GOOD, answers())],
        "steps": _paths({GOOD["token_key"]: [1.00, 1.05, 1.12, 1.20, 1.25, 1.22]}, 6),
        "faults": FaultPlan(timeout_after_fill={1}, lookup_unavailable={1, 2}),
    },
}


def judge_for(worth=0.8):
    def judge(qs, state):
        labels = [c["label"] for c in state["candidates"]]
        a = {"worth_trading_at_all": {"type": "noul", "noul": worth}}
        if len(labels) > 1:
            a["best"] = {"type": "choice", "choice": labels[0], "confidence": 0.8,
                         "probabilities": {lb: 0.8 if i == 0 else 0.2 / (len(labels) - 1)
                                           for i, lb in enumerate(labels)}}
        return {"model": "jev-synthetic", "answers": a}
    return judge


def run(name: str, starting_cash: float, journal_path: str | None = None) -> dict:
    sc = SCENARIOS[name]
    clock = session.SessionClock(T0)
    journal = RecordJournal(journal_path)
    market = StaticMarket()
    engines = build(":memory:", market, clock=clock, journal=journal,
                    starting_cash=starting_cash, faults=sc["faults"])
    session.start(engines, journal, clock)
    cands = [(d, a) for d, a in sc["candidates"]]
    stats = {"synthetic": True}
    for i, step in enumerate(sc["steps"]):
        ts = T0 + 300 * i
        for key, v in step.items():
            if v is None:
                market.fail.add(key)
            else:
                market.fail.discard(key)
                market.update(key, _quote(v[0], v[1], ts))
        session.poll(engines, journal, clock, ts)
        if i % 3 == 0:
            session.cycle(engines, journal, clock, ts + 1, lambda ask, free: (cands, stats),
                          judge_for())
    return {"engines": engines, "events": journal.events,
            "metrics": {n: metrics(e.ledger) for n, e in engines.items()}}


KEYS = ("return_pct", "realized_pnl_observed", "realized_pnl_assumed", "blind_closes",
        "stress_zero_recovery_return_pct", "closed_trades", "orders_ever_unknown",
        "fees_paid")


def table() -> str:
    rows = ["| scenario | bank | ledger | " + " | ".join(KEYS) + " |",
            "|" + "---|" * (len(KEYS) + 3)]
    for name in SCENARIOS:
        for bank in BANKS:
            r = run(name, bank)
            for ledger, m in r["metrics"].items():
                rows.append(f"| {name} | ${bank:,.0f} | {ledger} | " +
                            " | ".join(str(m[k]) for k in KEYS) + " |")
    return "\n".join(rows)


BANNER = """# SYNTHETIC RESULTS, NOT MARKET EVIDENCE

Generated by `python scenarios.py`. Every price, answer and outage below is invented to
exercise the paper engine. The `trap` scenario is rigged so the judge looks right. Nothing
here measures an edge, and nothing here should size a real position.

Market evidence: **none yet.** It can only come from paper runs on live data
(`python main.py`), compared with `python report.py` and checked with `python replay.py`.

At a $1,500 bank every entry is refused at the fee floor, so the rows show no trades. That
is the finding, not a bug: the fixed 3% placeholder of $1,500 is $45, and a $0.95 fee is
2.1% of it. Even the 6% cap ($90) stays under the $95 where the fee falls to 1%.

`realized_pnl_assumed` is the value of blind closes (no quote; last price minus the
haircut), an assumption. `stress_zero_recovery_return_pct` values every blind close and
every open position at $0.
"""


if __name__ == "__main__":
    out = os.path.join("examples", "synthetic")
    os.makedirs(out, exist_ok=True)
    for name in SCENARIOS:
        for bank in BANKS:
            path = os.path.join(out, f"{name}-{int(bank)}.jsonl")
            if os.path.exists(path):
                os.remove(path)
            run(name, bank, path)
    text = BANNER + "\n" + table() + "\n"
    with open(os.path.join("examples", "SYNTHETIC_RESULTS.md"), "w") as f:
        f.write(text)
    print(text)
    print(json.dumps({"journals": sorted(os.listdir(out))}))
