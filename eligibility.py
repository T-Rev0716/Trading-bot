"""One eligibility check for every candidate, whether it ends up alone or among many.

    check(d)            facts + missing-data policy. What the baseline uses.
    check(d, answers)   the same, then every required judge answer and the SOFT gates.
"""
from dataclasses import dataclass, field

from filter import chain_kill, soft_kill
from questions import SETS
from thresholds import MISSING_DATA

CHAIN_SET = {"solana": "solana", "bsc": "bsc", "base": "bsc", "robinhood": "robinhood"}


@dataclass
class Verdict:
    ok: bool
    reason: str | None = None
    size_factor: float = 1.0
    cuts: dict = field(default_factory=dict)       # field -> factor applied


def policy_for(name: str, chain: str):
    rule = MISSING_DATA[name]
    return rule.get(chain, rule.get("*", "reject"))


def missing_data(d: dict) -> Verdict:
    """Apply MISSING_DATA. Null never means fine: every field has a written consequence."""
    factor, cuts = 1.0, {}
    for name in MISSING_DATA:
        if d.get(name) is not None:
            continue
        rule = policy_for(name, d["chain"])
        if rule == "reject":
            return Verdict(False, f"missing:{name}")
        if rule == "allow":
            continue
        kind, f = rule
        if kind != "cut" or not 0 < f <= 1:
            raise ValueError(f"bad MISSING_DATA rule for {name}: {rule!r}")
        factor *= f
        cuts[name] = f
    return Verdict(True, None, round(factor, 4), cuts)


def required_answers(d: dict) -> set[str]:
    names = set(SETS["market"]) | set(SETS[CHAIN_SET[d["chain"]]])
    if d.get("x_account"):
        names |= set(SETS["social"])
    return names


def check(d: dict, answers: dict | None = None) -> Verdict:
    if not d.get("token_key"):
        return Verdict(False, "no_identity")
    if (k := chain_kill(d)):
        return Verdict(False, k)
    v = missing_data(d)
    if not v.ok or answers is None:
        return v
    missing = sorted(required_answers(d) - set(answers))
    if missing:
        return Verdict(False, f"missing_answer:{missing[0]}")
    if (k := soft_kill(answers)):
        return Verdict(False, k)
    return v
