"""CHIEF's call. The only judgement that ever sees more than one token at a time."""
from thresholds import PICK_MIN_WORTH, PICK_MIN_CONF, DARK_TICKET_CUT, NO_SOCIAL_CUT


def label(d) -> str:
    """Unique option name. Two launches sharing a ticker must not share an option."""
    return f"{d['ticker']} [{d['chain']}:{d['addr'][:6]}]"


def summary(d, ans) -> str:
    """Two lines per candidate, built from answers Jev already gave.
       Never the raw dossier. A fat state costs accuracy."""
    bits = [f"{d['chain']}, {d['age_minutes']:.0f}m old, ${d['mcap_usd']:,.0f} mcap, "
            f"${d['liquidity_usd']:,.0f} liq, {d['holder_count'] or '?'} holders",
            f"crowd {ans['shape']['probabilities'].get('crowd', 0):.2f}, "
            f"concentration risk {ans['concentration_is_exit_risk']['noul']:.2f}"]

    if "authority_risk" in ans:
        bits.append(f"authority {ans['authority_risk']['choice']}")
    if "sell_side_risk" in ans:
        bits.append(f"sell side {ans['sell_side_risk']['choice']}")
    if "data_coverage" in ans:
        bits.append(f"data {ans['data_coverage']['choice']}")
    if "account_is_the_project" in ans:
        bits.append(f"official account {ans['account_is_the_project']['noul']:.2f}, "
                    f"effort {ans['effort']['score']:.1f}")
    else:
        bits.append("no usable X account")
    return "; ".join(bits)


def size_factor(ans) -> float:
    """Less visibility, smaller ticket. Both cuts stack."""
    f = 1.0
    if (ans.get("data_coverage") or {}).get("choice") == "dark":
        f *= DARK_TICKET_CUT
    if "account_is_the_project" not in ans:
        f *= NO_SOCIAL_CUT
    return round(f, 2)


def order(d, ans, model, confidence, runner_up=None) -> dict:
    return {"model": model,
            "token": {"ticker": d["ticker"], "address": d["addr"],
                      "network_id": d["net"], "chain": d["chain"]},
            "liquidity_usd": d["liquidity_usd"],
            "size_factor": size_factor(ans),
            "confidence": confidence,
            "runner_up": runner_up or [],
            "why": dict(ans)}


def pick(judge, survivors) -> dict | None:
    """survivors: [(dossier, answers), ...] with at least two. Returns the order, or None."""
    if len(survivors) < 2:
        raise ValueError("pick needs two or more survivors. One goes straight to the bot.")

    by_label = {label(d): (d, a) for d, a in survivors}
    state = {"candidates": [{"label": label(d), "summary": summary(d, a)}
                            for d, a in survivors]}
    r = judge("pick", state)
    best, worth = r["answers"]["best"], r["answers"]["worth_trading_at_all"]

    if worth["noul"] < PICK_MIN_WORTH:
        return None                      # every candidate is mediocre. Normal outcome.
    if best["confidence"] < PICK_MIN_CONF:
        return None                      # flat over the options means no favourite.

    d, ans = by_label.get(best["choice"], (None, None))
    if d is None:
        return None                      # the schema guarantees the option is in the
                                         # list, so log this one and stand down.

    runner = sorted(best["probabilities"].items(), key=lambda kv: -kv[1])[1:2]
    return order(d, ans, r["model"], best["confidence"], runner)
