"""The order the kills fire in. Every number lives in thresholds.py.

Facts kill before judgements do. A null never passes a hard check: missing is missing.
"""
from thresholds import HARD, SOFT, SHAPE_MIN_CROWD


def _below(v, floor) -> bool:
    return v is None or v < floor


def free_kill(t) -> str | None:
    """Pass one. Runs on the whole universe, costs nothing, touches no network.
       Everything it reads came back with the FOMO batch."""
    age = t["age_minutes"]
    if age is None or not HARD["min_age_minutes"] <= age <= HARD["max_age_hours"] * 60:
        return "age"
    if _below(t["liquidity_usd"], HARD["min_liquidity_usd"]):    return "liquidity"
    if _below(t["volume_h24"], HARD["min_volume_h24"]):          return "volume"
    m = t["mcap_usd"]
    if m is None or not HARD["min_mcap_usd"] <= m <= HARD["max_mcap_usd"]:
        return "mcap"
    return None


def trade_kill(t) -> str | None:
    """Pass two. One DexScreener call already spent on this token. Tens, not hundreds."""
    if t["trades_h24"] is None:                                  return "no_pair"
    if t["trades_h24"] < HARD["min_trades_h24"]:                 return "trades"
    if t["sells_h1"] == 0 and (t["buys_h1"] or 0) > 20:          return "no_sells"
    return None


def chain_kill(d) -> str | None:
    """After the dossier, still free. Facts, not judgements."""
    if d.get("top_wallet_share") is not None and \
       d["top_wallet_share"] > HARD["max_top_wallet"]:
        return "top_wallet"
    if d.get("top_10_share") is not None and d["top_10_share"] > HARD["max_top_10"]:
        return "top_10"
    if d.get("holder_count") is not None and d["holder_count"] < HARD["min_holders"]:
        return "holders"
    if d["chain"] == "solana" and (d.get("mint_authority_open") is True or
                                   d.get("freeze_authority_open") is True):
        return "authority_open"          # a fact, no model needed
    if d.get("is_honeypot") is True:
        return "honeypot"                # also a fact, on any chain that reports it
    return None


def soft_kill(ans) -> str | None:
    """Jev's answers against SOFT. First failure wins."""
    for name, (direction, limit) in SOFT.items():
        a = ans.get(name)
        if a is None:
            continue                     # question not asked for this chain
        v = a.get("noul", a.get("score"))
        if v is None:
            continue
        if direction == "max" and v > limit: return name
        if direction == "min" and v < limit: return name

    shape = ans.get("shape")
    if shape:
        if shape["choice"] in ("fading", "one_buyer"):           return "shape"
        if shape["probabilities"].get("crowd", 0) < SHAPE_MIN_CROWD:
            return "shape_weak"

    chain = ans.get("sell_side_risk")
    if chain and chain["choice"] in ("flagged", "suspicious"):  return "sell_side"
    auth = ans.get("authority_risk")
    if auth and auth["choice"] in ("mint_open", "freeze_open", "both_open"):
        return "authority_risk"          # a judgement, so it does not bench like the fact
    return None
