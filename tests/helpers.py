"""Shared fixtures: complete dossiers, passing answers, quotes, engines on a fake clock."""
import itertools

from ids import token_key
from market import Quote, ReplayMarket
from paper import build

T0 = 1_700_000_000.0


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def dossier(addr="So1anaMint1111111111111111111111111111111111", chain="solana",
            ticker="AAA", **kw) -> dict:
    d = {"addr": addr, "net": {"solana": 1399811149, "bsc": 56, "base": 8453,
                               "robinhood": 4663}[chain],
         "chain": chain, "token_key": token_key(chain, addr), "ticker": ticker,
         "tid": f"{addr}:{chain}", "age_minutes": 90.0, "mcap_usd": 400_000.0,
         "liquidity_usd": 100_000.0, "volume_h24": 300_000.0, "price_usd": 1.0,
         "holder_count": 500, "change": {"5m": 0.01, "1h": 0.1, "24h": 0.3},
         "buys_h1": 60, "sells_h1": 40, "buys_h6": 300, "sells_h6": 250, "trades_h24": 900,
         "volume_h1": 15_000.0, "volume_h6": 80_000.0,
         "top_10_share": 0.30, "top_wallet_share": 0.02,
         "mint_authority_open": False, "freeze_authority_open": False,
         "is_honeypot": None if chain == "solana" else False,
         "developer_holding_percentage": 1.0, "gt_score_details": {"pool": 50},
         "x_handle": "coin", "x_account": {"handle": "coin"}}
    d.update(kw)
    return d


def _choice(label, p=0.8):
    return {"type": "choice", "choice": label, "confidence": p, "probabilities": {label: p}}


def answers(d, **override) -> dict:
    a = {"shape": {"type": "choice", "choice": "crowd", "confidence": 0.8,
                   "probabilities": {"crowd": 0.8, "one_buyer": 0.1, "fading": 0.05,
                                     "too_early": 0.05}},
         "liquidity_fits_ticket": {"type": "noul", "noul": 0.9},
         "momentum_already_spent": {"type": "noul", "noul": 0.2},
         "concentration_is_exit_risk": {"type": "noul", "noul": 0.1}}
    if d["chain"] == "solana":
        a |= {"authority_risk": _choice("renounced"),
              "dev_still_loaded": {"type": "noul", "noul": 0.1}}
    elif d["chain"] in ("bsc", "base"):
        a |= {"sell_side_risk": _choice("clean"),
              "pool_quality": {"type": "score", "score": 2.0}}
    else:
        a |= {"data_coverage": _choice("indexed"),
              "sellable_by_evidence": {"type": "noul", "noul": 0.9},
              "dev_still_loaded": {"type": "noul", "noul": 0.1}}
    if d.get("x_account"):
        a |= {"account_is_the_project": {"type": "noul", "noul": 0.95},
              "audience_is_real": {"type": "noul", "noul": 0.8},
              "recycled_account": {"type": "noul", "noul": 0.05},
              "effort": {"type": "score", "score": 2.0}}
    a.update(override)
    return a


def quote(price=1.0, liq=100_000.0, v6=80_000.0, v24=300_000.0, ts=T0) -> Quote:
    return Quote(price_usd=price, liquidity_usd=liq, volume_h6=v6, volume_h24=v24, ts=ts)


def engines(clock=None, faults=None, names=("strategy", "baseline")):
    clock = clock or Clock()
    ids = itertools.count(1)
    market = ReplayMarket()
    return build(":memory:", market, clock=clock, faults=faults, names=names,
                 new_id=lambda: f"id{next(ids):04d}"), market, clock


def pick_judge(choose=None, worth=0.9, conf=0.8, calls=None):
    """A judge for the pick set: picks `choose` (a label) or the first candidate."""
    def judge(qs, state):
        if calls is not None:
            calls.append((qs, state))
        assert qs == "pick"
        labels = [c["label"] for c in state["candidates"]]
        ans = {"worth_trading_at_all": {"type": "noul", "noul": worth}}
        if len(labels) >= 2:
            c = choose or labels[0]
            rest = (1 - conf) / (len(labels) - 1)
            ans["best"] = {"type": "choice", "choice": c, "confidence": conf,
                           "probabilities": {l: conf if l == c else rest for l in labels}}
        return {"model": "jev-test", "answers": ans}
    return judge
