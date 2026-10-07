"""The strategy's selection: one judge call over every eligible candidate.

Every candidate reaching here already passed eligibility.check with its answers. One
candidate or ten, the same absolute gate (worth_trading_at_all) and the same confidence
gate apply. With one candidate the choice is certain by construction (confidence 1.0),
so the confidence gate passes and the absolute gate decides, as it does for many.
"""
from ids import label
from thresholds import PICK_MIN_CONF, PICK_MIN_WORTH


class PickUnavailable(RuntimeError):
    """No pick answer exists for this candidate set (replay without a recording)."""


def summary(d, ans, verdict) -> str:
    """Two lines per candidate, built from answers Jev already gave.
       Never the raw dossier. A fat state costs accuracy."""
    bits = [f"{d['chain']}, {d['age_minutes']:.0f}m old, ${d['mcap_usd']:,.0f} mcap, "
            f"${d['liquidity_usd']:,.0f} liq, {d['holder_count']} holders",
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
    if verdict.cuts:
        bits.append("missing " + ", ".join(sorted(verdict.cuts)))
    return "; ".join(bits)


def pick_state(eligible) -> dict:
    return {"candidates": [{"label": label(d), "summary": summary(d, a, v)}
                           for d, a, v in eligible]}


def select(judge, eligible) -> tuple[tuple | None, dict]:
    """eligible: [(dossier, answers, verdict)]. Returns ((d, ans, verdict) | None, info)."""
    if not eligible:
        return None, {"note": "no eligible candidate"}
    by_label = {label(d): (d, a, v) for d, a, v in eligible}
    if len(by_label) != len(eligible):
        raise ValueError("two candidates share a chain:address")
    state = pick_state(eligible)
    r = judge("pick", state)
    worth = r["answers"]["worth_trading_at_all"]["noul"]
    info = {"model": r["model"], "worth": worth, "labels": list(by_label), "response": r}
    if worth < PICK_MIN_WORTH:
        return None, {**info, "note": f"worth_trading_at_all {worth:.2f}"}

    if len(eligible) == 1:
        choice, confidence, runner = next(iter(by_label)), 1.0, []
    else:
        best = r["answers"]["best"]
        choice, confidence = best["choice"], best["confidence"]
        runner = sorted(best["probabilities"].items(), key=lambda kv: -kv[1])[1:2]
    info |= {"choice": choice, "confidence": confidence, "runner_up": runner}
    if confidence < PICK_MIN_CONF:
        return None, {**info, "note": f"pick confidence {confidence:.2f}"}
    if choice not in by_label:
        return None, {**info, "note": f"pick returned unknown option {choice!r}"}
    return by_label[choice], info
