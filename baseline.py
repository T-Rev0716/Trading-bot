"""Rules-only baseline. Same candidates, same eligibility, same sizing, same exits, same
fills as the strategy; the only thing missing is the judge. If the strategy cannot beat
this on paper, the judge is not adding anything."""
from thresholds import BASELINE


def _share(b, s):
    return b / (b + s) if b is not None and s is not None and b + s > 0 else None


def rules(d: dict, cfg=BASELINE) -> str | None:
    """None when the candidate passes, else the rule that failed."""
    s1 = _share(d.get("buys_h1"), d.get("sells_h1"))
    s6 = _share(d.get("buys_h6"), d.get("sells_h6"))
    if s1 is None or s6 is None:
        return "flow_missing"
    if s1 < cfg["min_buy_share_h1"]:
        return "crowd_h1"
    if s6 < cfg["min_buy_share_h6"]:
        return "crowd_h6"
    v1, v24 = d.get("volume_h1"), d.get("volume_h24")
    if v1 is None or not v24:
        return "volume_missing"
    if v1 / v24 < cfg["min_h1_volume_share"]:
        return "fading"
    c1 = (d.get("change") or {}).get("1h")
    if c1 is not None and c1 > cfg["max_change_1h"]:
        return "momentum_spent"
    return None


def score(d: dict) -> tuple:
    """Higher is better. Buy share, then turnover, then the key so ties are deterministic."""
    return (_share(d["buys_h1"], d["sells_h1"]),
            (d.get("volume_h24") or 0) / max(d.get("mcap_usd") or 0, 1),
            d["token_key"])


def select(eligible) -> tuple | None:
    """eligible: [(dossier, verdict)] that passed eligibility.check. Returns one or None."""
    passing = [(d, v) for d, v in eligible if rules(d) is None]
    return max(passing, key=lambda x: score(x[0])) if passing else None
