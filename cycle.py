"""Entries for one cycle, shared by the live paper shift and by replay, so both run the
exact same selection code."""
import eligibility
import baseline
import pick
from ids import label


def enter(candidates, judge, engines: dict, cycle_id: str) -> dict:
    """candidates: [(dossier, answers or None)]. answers is None when the judge was not
       asked (strategy blocked, or the token benched for the strategy only).

       Each engine is entered only if it can take a new order right now."""
    out = {}
    strat, base = engines.get("strategy"), engines.get("baseline")

    if strat is not None and strat.can_enter() is None:
        elig = []
        for d, a in candidates:
            if a is None:
                continue
            v = eligibility.check(d, a)
            if v.ok:
                elig.append((d, a, v))
        try:
            chosen, info = pick.select(judge, elig)
        except pick.PickUnavailable as e:
            chosen, info = None, {"note": f"pick unavailable: {e}"}
        if chosen:
            d, a, v = chosen
            res = strat.enter(d, v.size_factor, cycle_id,
                              meta={"label": label(d), "cuts": v.cuts,
                                    "model": info["model"], "confidence": info["confidence"],
                                    "why": a})
            info |= {"entry": res}
        out["strategy"] = {k: v for k, v in info.items() if k != "response"}
        out["strategy_pick"] = info.get("response") and {"labels": info["labels"],
                                                         "response": info["response"]}

    if base is not None and base.can_enter() is None:
        elig = []
        for d, _ in candidates:
            v = eligibility.check(d)
            if v.ok:
                elig.append((d, v))
        chosen = baseline.select(elig)
        info = {"eligible": len(elig)}
        if chosen:
            d, v = chosen
            info |= {"choice": label(d),
                     "entry": base.enter(d, v.size_factor, cycle_id,
                                         meta={"label": label(d), "cuts": v.cuts})}
        else:
            info["note"] = "no candidate passed the baseline rules"
        out["baseline"] = info
    return out
