"""Bounded rejection evidence: what the screen actually saw when it said no.

At most LIMIT examples per reason per cycle, plus the full count. Each example names the
token (chain and full address), the data source and the source field, the sanitized raw
value, the parsed value and its parse status, the threshold it was held to, and two
separate times:

    fetched_at_local     epoch seconds on THIS machine's clock when the FOMO batch came
                         back. It is not a market timestamp.
    provider_timestamp   a timestamp field FOMO itself sent for the row, as
                         {"field", "value"} exactly as received, or null if it sent none

The evidence is part of the scan result, which is journaled, so replay and later reading
need no external call.
"""
import sanitize
from thresholds import HARD

LIMIT = 5

# reason prefix -> (desk field, threshold)
_FIELDS = {
    "age": ("age_minutes", lambda: {"min_minutes": HARD["min_age_minutes"],
                                    "max_minutes": HARD["max_age_hours"] * 60}),
    "liquidity": ("liquidity_usd", lambda: {"min": HARD["min_liquidity_usd"]}),
    "volume": ("volume_h24", lambda: {"min": HARD["min_volume_h24"]}),
    "mcap": ("mcap_usd", lambda: {"min": HARD["min_mcap_usd"], "max": HARD["max_mcap_usd"]}),
}


def example(t: dict, reason: str) -> dict:
    """One rejection, as the screen saw it. Only for screen (free_kill) reasons."""
    field, threshold = _FIELDS[reason.split("_", 1)[0]]
    q = (t.get("data_quality") or {}).get(field) or {}
    return {"token_key": t.get("token_key"), "tid": t.get("tid"), "ticker": t.get("ticker"),
            "source": q.get("source"), "source_field": q.get("source_field"),
            "raw": sanitize.data(q.get("raw")), "parse_status": q.get("status"),
            "invalid_kind": q.get("invalid_kind"), "parsed": t.get(field),
            "threshold": threshold(),
            "fetched_at_local": t.get("fetched_at_local"),
            "provider_timestamp": sanitize.data(t.get("provider_timestamp"))}


class EvidenceLog:
    def __init__(self, limit: int = LIMIT):
        self.limit, self.by_reason = limit, {}

    def add(self, reason: str, t: dict):
        slot = self.by_reason.setdefault(reason, {"count": 0, "examples": []})
        slot["count"] += 1
        if len(slot["examples"]) < self.limit:
            slot["examples"].append(example(t, reason))

    def dump(self) -> dict:
        return {r: {"count": v["count"], "examples": list(v["examples"])}
                for r, v in sorted(self.by_reason.items())}
