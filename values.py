"""Strict parsing of provider numbers. Nothing is silently turned into zero or dropped.

    parse_metric(raw) -> Metric(value, status, kind)

    status "ok"       a finite, non-negative number. Zero is an observed value, kept as 0.0.
    status "missing"  the field was absent, null or an empty string
    status "invalid"  present but unusable; `kind` says why:
                      "nan", "infinite", "negative", "malformed" (not a number, or a
                      boolean, or text such as "1,234" that would need guessing)

Only an "ok" metric carries a value. An invalid one has value None and is rejected for
being invalid, never treated as missing and never as zero.
"""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    value: float | None
    status: str                  # ok | missing | invalid
    kind: str | None = None      # nan | infinite | negative | malformed


def parse_metric(raw) -> Metric:
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return Metric(None, "missing")
    if isinstance(raw, bool):
        return Metric(None, "invalid", "malformed")
    if isinstance(raw, (int, float)):
        v = float(raw)
    elif isinstance(raw, str):
        try:
            v = float(raw.strip())
        except ValueError:
            return Metric(None, "invalid", "malformed")
    else:
        return Metric(None, "invalid", "malformed")
    if math.isnan(v):
        return Metric(None, "invalid", "nan")
    if math.isinf(v):
        return Metric(None, "invalid", "infinite")
    if v < 0:
        return Metric(None, "invalid", "negative")
    return Metric(v, "ok")


def raw_for_record(raw):
    """A JSON-safe, comparable form of a raw provider value: non-finite floats and
       unexpected types become strings, long strings are cut."""
    if isinstance(raw, float) and not math.isfinite(raw):
        return repr(raw)
    if raw is None or isinstance(raw, (bool, int, float)):
        return raw
    if isinstance(raw, str):
        return raw[:80]
    return repr(raw)[:80]
