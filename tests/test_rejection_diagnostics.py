"""Rejection diagnostics: strict metric parsing, split reasons, bounded evidence, and the
read-only liquidity cross-check.

Every provider row and response in this file is a SYNTHETIC sample written for these
tests, shaped like the FOMO rows and DexScreener bodies the parsers expect. None is a
recording, and none verifies a provider. No real fixtures were available to use here."""
import copy

import pytest

import book
import crosscheck
import fomo_api
import main
import replay
import session
from collect import normalise
from diagnose import Blocked, Fetcher
from filter import free_kill
from journal import RecordJournal
from market import StaticMarket
from paper import build
from tests.helpers import T0
from values import parse_metric
from watchlist import Watchlist

MIN = 60
ADDR = "DiagMint1111111111111111111111111111111111111"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkaWFnIn0.c2lnbmF0dXJlc2ln"


def row(**over):
    """A synthetic FOMO filterTokens row, Codex-shaped. Healthy unless overridden."""
    r = {"token": {"address": ADDR, "networkId": 1399811149, "symbol": "DIAG"},
         "marketCap": "400000", "liquidity": "50000", "volume24": "300000",
         "priceUSD": "0.001", "holders": 500, "change1": "0.1",
         "createdAt": int(T0 - 60 * MIN)}
    r.update(over)
    return r


def screened(**over):
    m = {**fomo_api._row(row(**over)), "fetched_at": T0 - 2}
    return normalise(f"{ADDR}:1399811149", m, now=T0)


# ---- strict parsing ----------------------------------------------------------------------
@pytest.mark.parametrize("raw,value,status,kind", [
    (0, 0.0, "ok", None), ("0", 0.0, "ok", None), ("0.0", 0.0, "ok", None),
    ("12000.5", 12000.5, "ok", None), (7, 7.0, "ok", None),
    (None, None, "missing", None), ("", None, "missing", None), ("  ", None, "missing", None),
    (float("nan"), None, "invalid", "nan"), ("NaN", None, "invalid", "nan"),
    (float("inf"), None, "invalid", "infinite"), ("Infinity", None, "invalid", "infinite"),
    ("-inf", None, "invalid", "infinite"), (-1, None, "invalid", "negative"),
    ("-0.01", None, "invalid", "negative"), ("1,234", None, "invalid", "malformed"),
    ("abc", None, "invalid", "malformed"), (True, None, "invalid", "malformed"),
    ({"usd": 1}, None, "invalid", "malformed"),
])
def test_parse_metric(raw, value, status, kind):
    m = parse_metric(raw)
    assert (m.value, m.status, m.kind) == (value, status, kind)


def test_nan_liquidity_no_longer_passes_the_screen():
    """regression: float('NaN') < 12000 is False, so NaN used to pass the minimum"""
    t = screened(liquidity="NaN")
    assert t["liquidity_usd"] is None
    assert free_kill(t) == "liquidity_invalid"


def test_a_malformed_holder_count_does_not_break_the_batch():
    """regression: int('abc') raised and took the whole 20-token batch with it"""
    assert fomo_api._row(row(holders="abc"))["holders"] is None


# ---- split reasons -------------------------------------------------------------------------
@pytest.mark.parametrize("over,reason", [
    ({"liquidity": None}, "liquidity_missing"),
    ({"liquidity": ""}, "liquidity_missing"),
    ({"liquidity": "0"}, "liquidity_below_min"),            # zero is observed, not missing
    ({"liquidity": 0}, "liquidity_below_min"),
    ({"liquidity": "11999.99"}, "liquidity_below_min"),
    ({"liquidity": "NaN"}, "liquidity_invalid"),
    ({"liquidity": "Infinity"}, "liquidity_invalid"),
    ({"liquidity": "-5"}, "liquidity_invalid"),
    ({"liquidity": "50k"}, "liquidity_invalid"),
    ({"volume24": None}, "volume_missing"),
    ({"volume24": "0"}, "volume_below_min"),
    ({"volume24": "-1"}, "volume_invalid"),
    ({"marketCap": None}, "mcap_missing"),
    ({"marketCap": "59999"}, "mcap_below_min"),
    ({"marketCap": "8000001"}, "mcap_above_max"),
    ({"marketCap": "inf"}, "mcap_invalid"),
    ({}, None),
    ({"liquidity": "12000"}, None),                        # thresholds unchanged, inclusive
    ({"marketCap": "8000000"}, None),
])
def test_screen_reasons(over, reason):
    assert free_kill(screened(**over)) == reason


def test_rejection_order_is_unchanged():
    assert free_kill(screened(liquidity="0", volume24="NaN", marketCap=None)) == \
        "liquidity_below_min"
    assert free_kill(screened(volume24="NaN", marketCap=None)) == "volume_invalid"
    assert free_kill(screened(createdAt=None, liquidity="NaN")) == "age_missing"


def test_new_reasons_keep_the_old_bench_and_stay_watched():
    for r in ("liquidity_missing", "liquidity_invalid", "liquidity_below_min",
              "volume_missing", "volume_invalid", "volume_below_min", "mcap_missing",
              "mcap_invalid", "mcap_below_min", "mcap_above_max"):
        assert book.bench_minutes(r) == book.BENCH_MINUTES["liquidity"] == 25, r
        assert book.bench_minutes(r) < 72 * 60            # so the watchlist keeps them


# ---- evidence in the scan ---------------------------------------------------------------
def addr(i):
    return f"DiagMint{i:02d}" + "1" * 35


class Fomo:
    """Synthetic FOMO: parses Codex-shaped rows through the real _row."""

    def __init__(self, rows, fetched_at):
        self.rows, self.fetched_at = rows, fetched_at

    def tokens(self, ids):
        out = {}
        for tid in ids:
            if tid in self.rows:
                out[tid] = {**fomo_api._row(self.rows[tid]), "fetched_at": self.fetched_at}
        return out


@pytest.fixture
def wired(monkeypatch):
    pages = {"now": []}
    monkeypatch.setattr(main, "universe", lambda nets, p: list(pages["now"]))
    monkeypatch.setattr(main, "trade_counts", lambda t: pytest.fail("screen should reject"))
    return pages


def rows_for(n, **over):
    out = {}
    for i in range(n):
        r = row(**over)
        r["token"] = {**r["token"], "address": addr(i)}
        out[f"{addr(i)}:1399811149"] = r
    return out


def test_evidence_is_bounded_and_complete(wired):
    rows = rows_for(7, liquidity="0")
    rows[f"{addr(0)}:1399811149"]["updatedAt"] = 1791400000      # a provider timestamp
    wired["now"] = list(rows)
    watch = Watchlist(book.DB, 72, 200)
    _, st = main.scan(Fomo(rows, T0 - 2), None, None, 10_000.0, False, now=T0, watch=watch)
    ev = st["evidence"]["liquidity_below_min"]
    assert st["free"] == {"liquidity_below_min": 7}
    assert ev["count"] == 7 and len(ev["examples"]) == 5
    x = ev["examples"][0]
    assert x["token_key"] == f"solana:{addr(0)}"                # chain and full address
    assert (x["source"], x["source_field"]) == ("fomo", "liquidity")
    assert (x["raw"], x["parsed"], x["parse_status"]) == ("0", 0.0, "ok")
    assert x["threshold"] == {"min": 12_000}
    assert x["fetched_at_local"] == T0 - 2                      # our clock
    assert x["provider_timestamp"] == {"field": "updatedAt", "value": 1791400000}
    assert ev["examples"][1]["provider_timestamp"] is None      # never invented
    assert all(watch.entry(t) for t in rows)                    # still watched


def test_evidence_records_invalid_kinds_and_sanitizes_raw(wired):
    rows = {**rows_for(1, liquidity="NaN")}
    leak = row(liquidity=JWT)
    leak["token"] = {**leak["token"], "address": addr(5)}
    rows[f"{addr(5)}:1399811149"] = leak
    wired["now"] = list(rows)
    _, st = main.scan(Fomo(rows, T0), None, None, 1.0, False, now=T0,
                      watch=Watchlist(book.DB, 72, 200))
    ex = st["evidence"]["liquidity_invalid"]["examples"]
    assert {(e["raw"], e["invalid_kind"]) for e in ex} == {("NaN", "nan"),
                                                           ("[REDACTED]", "malformed")}
    assert all(e["parsed"] is None for e in ex)


def _record(tmp_path, wired, rows):
    run = tmp_path / "run"
    run.mkdir()
    clock = session.SessionClock(T0)
    j = RecordJournal(str(run / "journal.jsonl"))
    eng = build(str(run / "paper.db"), StaticMarket(), clock=clock, journal=j)
    session.start(eng, j, clock)
    watch = Watchlist(book.DB, 72, 200)
    wired["now"] = list(rows)
    for i in range(2):
        ts = T0 + 900 * i
        session.poll(eng, j, clock, ts)
        session.cycle(eng, j, clock, ts + 1, lambda ask, free: main.scan(
            Fomo(rows, clock()), None, None, free, False, now=clock(), watch=watch), None)
        book.DB.execute("DELETE FROM bench_v2")
    return run, j.events


def test_evidence_is_journaled_and_replay_needs_no_calls(tmp_path, wired):
    run, events = _record(tmp_path, wired, rows_for(3, liquidity="5000"))
    scans = crosscheck.scan_results(events)
    assert len(scans) == 2
    assert scans[0]["stats"]["evidence"]["liquidity_below_min"]["count"] == 3
    _, diffs = replay.compare_run(str(run))                     # no network in replay
    assert diffs == []


# ---- the read-only liquidity cross-check ------------------------------------------------
def test_selection_policy_newest_cycle_first_deduped_and_capped(tmp_path, wired):
    rows = rows_for(4, liquidity="5000")
    rows.update({k.replace("DiagMint", "DiagMinu"): {**v, "token": {
        **v["token"], "address": v["token"]["address"].replace("DiagMint", "DiagMinu")},
        "liquidity": None} for k, v in rows_for(3).items()})
    _, events = _record(tmp_path, wired, rows)
    picked = crosscheck.select_liquidity_rejections(events)
    assert len(picked) == 5
    assert [p["reason"] for p in picked] == ["liquidity_below_min"] * 4 + \
        ["liquidity_missing"]
    assert len({p["token_key"] for p in picked}) == 5
    assert picked[0]["cycle_id"] == crosscheck.scan_results(events)[-1]["cycle_id"]


def pair(chain, paddr, liq, base=ADDR, dex="raydium"):
    p = {"chainId": chain, "pairAddress": paddr, "dexId": dex,
         "baseToken": {"address": base, "symbol": "DIAG"},
         "quoteToken": {"address": "So11111111111111111111111111111111111111112",
                        "symbol": "SOL"}}
    if liq is not ...:
        p["liquidity"] = {"usd": liq}
    return p


class DexFetcher(Fetcher):
    def __init__(self, body=None, exc=None):
        super().__init__()
        self.body, self.exc, self.urls = body, exc, []

    def _call(self, method, url, **kw):
        self.urls.append(url)
        if self.exc:
            raise self.exc
        return 200, self.body


KEY = f"solana:{ADDR}"


def test_pairs_are_listed_not_summed_and_the_deepest_is_selected():
    body = {"pairs": [pair("solana", "PairSmall", 4000.0), pair("solana", "PairDeep", 9000.0),
                      pair("bsc", "PairOther", 1e9)]}
    f = DexFetcher(body)
    d = crosscheck.dexscreener_pairs(f, KEY)
    assert f.urls == [f"{crosscheck.DEX}/{ADDR}"]               # same full address
    assert d["status"] == "ok" and d["other_chain_pairs"] == 1
    assert [p["pair_address"] for p in d["pairs"]] == ["PairSmall", "PairDeep"]
    assert d["selected_pair"] == "PairDeep" and d["selected_liquidity_usd"] == 9000.0
    assert "not summed" in d["selection_policy"]
    assert all(p["token_role"] == "base" for p in d["pairs"])


@pytest.mark.parametrize("body,status", [
    ({"pairs": []}, "no_pairs"),
    ({"pairs": None}, "no_pairs"),
    ({"pairs": [pair("bsc", "PairOther", 1e6)]}, "no_pairs_on_chain"),
    ({"pairs": [pair("solana", "PairA", ...), pair("solana", "PairB", None)]},
     "liquidity_missing"),
    ({"pairs": [pair("solana", "PairA", "NaN")]}, "liquidity_missing"),
])
def test_empty_pairs_and_missing_liquidity_are_reported_apart(body, status):
    d = crosscheck.dexscreener_pairs(DexFetcher(body), KEY)
    assert d["status"] == status
    assert d["selected_liquidity_usd"] is None


def test_blocked_dexscreener_is_reported_not_guessed():
    d = crosscheck.dexscreener_pairs(DexFetcher(exc=Blocked("egress proxy refused")), KEY)
    assert d["status"] == "BLOCKED" and d["pairs"] == []


def test_crosscheck_compares_without_substituting(tmp_path, wired):
    _, events = _record(tmp_path, wired, rows_for(1, liquidity="5000"))
    before = copy.deepcopy(events)
    toks = crosscheck.select_liquidity_rejections(events)
    body = {"pairs": [pair("solana", "P1", 10_000.0)]}
    body["pairs"][0]["baseToken"]["address"] = addr(0)
    rows = crosscheck.crosscheck(toks, DexFetcher(body), refetch_fomo=False)
    c = rows[0]["comparison"]
    assert c["fomo_value_used"] == "recorded" and c["fomo_liquidity_usd"] == 5000.0
    assert c["dexscreener_selected_pair_liquidity_usd"] == 10_000.0
    assert c["ratio_dex_over_fomo"] == 2.0
    assert rows[0]["fomo"]["recorded"]["fetched_at_local"] is not None
    assert events == before                                     # nothing written back
    assert "policy:" in crosscheck.render_crosscheck(rows)
    assert "liquidity_below_min x1" in crosscheck.render_evidence(events)


def test_crosscheck_is_capped_at_five():
    toks = [{"token_key": f"solana:{addr(i)}"} for i in range(8)]
    f = DexFetcher({"pairs": []})
    assert len(crosscheck.crosscheck(toks, f, refetch_fomo=False)) == 5 == len(f.urls)


def test_trading_loop_never_calls_the_crosscheck():
    import glob
    import os
    from tests.conftest import ROOT
    for path in glob.glob(os.path.join(ROOT, "*.py")):
        if os.path.basename(path) not in ("crosscheck.py",):
            assert "crosscheck" not in open(path).read(), path
