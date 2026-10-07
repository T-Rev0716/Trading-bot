"""Watchlist churn at capacity, and top_10 rejection evidence.

All token rows and GeckoTerminal bodies here are SYNTHETIC samples written for these
tests. None is a recording."""
import sqlite3

import pytest

import book
import collect
import fomo_api
import main
import replay
import session
from collect import parse_share
from eligibility import check
from filter import chain_kill
from journal import RecordJournal
from market import StaticMarket
from paper import build
from tests.helpers import T0, dossier
from watchlist import Watchlist

MIN = 60
CAP = 200


def tid(prefix, i):
    return f"{prefix}{i:03d}" + "1" * 36 + ":1399811149"


def full(db=None) -> Watchlist:
    """A full watchlist: 200 tokens discovered one minute apart, oldest first."""
    w = Watchlist(db or book.DB, 72, CAP)
    for i in range(CAP):
        w.apply_batch([(tid("Old", i), T0 - 10 * 3600)], (), T0 - (CAP - i) * MIN)
    assert w.size() == CAP
    return w


def first_seen(w) -> dict:
    return {r[0]: r[1] for r in w.db.execute("SELECT tid, first_seen FROM watchlist")}


# ---- the churn regression -------------------------------------------------------------
def test_full_watchlist_plus_new_discoveries_evicts_each_slot_once():
    w = full()
    before = first_seen(w)
    new = [(tid("New", i), T0 - 5 * MIN) for i in range(33)]
    # the batch as a scan sees it: new discoveries first (higher turnover), then every
    # watched token re-fetched, the OLDEST ones included
    old = [(t, T0 - 10 * 3600) for t in sorted(before)]
    r = w.apply_batch(new + old, (), T0)
    assert r["eviction_operations"] == 33 == r["removed_unique"]
    assert r["inserted"] == 33 and r["inserted_then_evicted"] == 0
    assert r["size"] == w.size() == CAP
    after = first_seen(w)
    oldest_33 = sorted(before, key=lambda t: (before[t], t))[:33]
    assert set(r["evicted_tids"]) == set(oldest_33)
    assert not set(oldest_33) & set(after)                  # not re-inserted this batch
    for t in set(before) & set(after):
        assert after[t] == before[t], f"{t} had its first_seen reset"
    assert {t for t, fs in after.items() if fs == T0} == {t for t, _ in new}


@pytest.fixture
def funnel(monkeypatch):
    pages = {"now": []}
    monkeypatch.setattr(main, "universe", lambda nets, p: list(pages["now"]))
    monkeypatch.setattr(main, "trade_counts", lambda t: {
        "buys_h1": 60, "sells_h1": 40, "buys_h6": 300, "sells_h6": 250, "trades_h24": 900,
        "volume_h1": 15_000.0, "volume_h6": 80_000.0})
    return pages


class Fomo:
    """Synthetic FOMO rows through the real parser. Turnover sets the scan's row order."""

    def __init__(self, rows):
        self.rows = rows

    def tokens(self, ids):
        return {t: {**fomo_api._row(self.rows[t]), "fetched_at": T0 - 2}
                for t in ids if t in self.rows}


def row(t, created, vol, liquidity="50000", top10=None):
    return {"token": {"address": t.split(":")[0], "networkId": 1399811149, "symbol": "S"},
            "marketCap": "400000", "liquidity": liquidity, "volume24": str(vol),
            "priceUSD": "0.001", "holders": 500, "createdAt": int(created)}


def test_scan_at_capacity_keeps_first_seen_and_reports_unique_removals(funnel):
    """End to end through main.scan. Row by row, the first new token evicted Old000,
       whose own row (later in the batch) re-inserted it with first_seen = now, which
       evicted Old001, and so on."""
    w = full()
    before = first_seen(w)
    rows = {}
    for i in range(33):                                     # young: watched, not judged
        rows[tid("New", i)] = row(tid("New", i), T0 - 5 * MIN, vol=900_000)
    for t in before:
        rows[t] = row(t, T0 - 10 * 3600, vol=100_000, liquidity="1")   # screened out
    funnel["now"] = [tid("New", i) for i in range(33)]
    _, st = main.scan(Fomo(rows), None, None, 10_000.0, False, now=T0, watch=w)
    d = st["discovery"]
    assert d["eviction_operations"] == 33
    assert d["removed_tokens"] == {"expired": 0, "dropped": 0, "evicted": 33,
                                   "total_unique": 33}
    assert d["inserted_then_evicted"] == 0 and d["watchlist_size"] == CAP
    after = first_seen(w)
    assert all(after[t] == before[t] for t in set(before) & set(after))
    assert set(before) - set(after) == set(sorted(before, key=lambda t: before[t])[:33])


def test_duplicates_in_one_batch_are_one_entry():
    w = Watchlist(book.DB, 72, CAP)
    r = w.apply_batch([("A", None), ("A", T0 - MIN), ("A", None)], (), T0)
    assert r["inserted"] == 1 and w.size() == 1
    e = w.entry("A")
    assert e["created_at"] == T0 - MIN and e["times_seen"] == 1


def test_a_drop_wins_and_is_not_an_eviction():
    w = full()
    victim = tid("Old", 150)
    r = w.apply_batch([(victim, None), (tid("New", 0), None)], {victim}, T0)
    assert r["dropped"] == 1 and r["eviction_operations"] == 0
    assert r["removed_unique"] == 1 and w.entry(victim) is None and w.size() == CAP


def test_more_new_than_capacity_is_deterministic():
    w = Watchlist(book.DB, 72, 5)
    names = [f"N{i}" for i in (6, 2, 0, 4, 1, 5, 3)]
    r = w.apply_batch([(n, None) for n in names], (), T0)
    assert r["eviction_operations"] == 2 == r["inserted_then_evicted"]
    assert sorted(w.active(T0)) == ["N2", "N3", "N4", "N5", "N6"]   # (first_seen, tid)


def test_the_order_of_a_batch_does_not_change_the_result(tmp_path):
    def run(order):
        w = full(sqlite3.connect(str(tmp_path / f"{order}.db")))
        obs = [(tid("New", i), None) for i in range(40)] + \
              [(t, None) for t in first_seen(w)]
        w.apply_batch(obs if order == "a" else obs[::-1], (), T0)
        return first_seen(w)
    assert run("a") == run("b")


def test_capacity_and_first_seen_survive_a_restart(tmp_path):
    path = str(tmp_path / "desk.db")
    w = full(sqlite3.connect(path))
    w.apply_batch([(tid("New", i), None) for i in range(10)], (), T0)
    expected = first_seen(w)
    w.db.close()
    again = Watchlist(sqlite3.connect(path), 72, CAP)
    assert again.size() == CAP and first_seen(again) == expected


# ---- top_10 ------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,share,status,kind", [
    ("41.5", 0.415, "ok", None), ("0", 0.0, "ok", None), ("100", 1.0, "ok", None),
    (None, None, "missing", None), ("", None, "missing", None),
    ("NaN", None, "invalid", "nan"), ("Infinity", None, "invalid", "infinite"),
    ("-1", None, "invalid", "negative"), ("abc", None, "invalid", "malformed"),
    ("150", None, "invalid", "above_100_percent"),
])
def test_parse_share(raw, share, status, kind):
    s, q = parse_share(raw)
    assert (s, q["status"], q["invalid_kind"]) == (pytest.approx(share) if share else share,
                                                    status, kind)
    assert q["source"] == "geckoterminal"


def dq(raw):
    s, q = parse_share(raw)
    return {"top_10_share": s, "data_quality": {"top_10_share": q}}


@pytest.mark.parametrize("raw,reason", [
    ("75.5", "top_10_above_max"), ("60.01", "top_10_above_max"), ("60", None),
    ("NaN", "top_10_invalid"), ("Infinity", "top_10_invalid"), ("150", "top_10_invalid"),
    ("-3", "top_10_invalid"), (None, None),
])
def test_top_10_reasons(raw, reason):
    assert chain_kill(dossier(**dq(raw))) == reason


def test_nan_top_10_is_invalid_not_a_pass():
    """regression: nan > 0.60 is False and nan is not None, so a NaN share used to pass
       the check AND escape the missing-data cut"""
    assert chain_kill(dossier(top_10_share=float("nan"))) == "top_10_invalid"


def test_missing_top_10_is_still_a_cut_not_a_rejection():
    v = check(dossier(**dq(None)))
    assert v.ok and v.cuts == {"top_10_share": 0.5}


def gt_body(top10):
    return {"data": {"attributes": {
        "holders": {"count": 500, "distribution_percentage": {"top_10": top10}},
        "mint_authority": "no", "freeze_authority": "no", "twitter_handle": None}}}


def test_top_10_evidence_is_recorded_bounded_and_labelled(funnel, monkeypatch):
    monkeypatch.setattr(main, "GT_DOSSIER", 10)
    monkeypatch.setattr(collect, "gt_get", lambda path: gt_body("75.5"))
    monkeypatch.setattr(collect, "sol_top_wallet", lambda mint: 0.01)
    monkeypatch.setattr(main, "dossier", collect.dossier)
    rows = {tid("Top", i): row(tid("Top", i), T0 - 60 * MIN, vol=300_000) for i in range(7)}
    funnel["now"] = list(rows)
    _, st = main.scan(Fomo(rows), None, None, 10_000.0, False, now=T0,
                      watch=Watchlist(book.DB, 72, CAP))
    assert st["chain"] == {"top_10_above_max": 7}
    ev = st["evidence"]["top_10_above_max"]
    assert ev["count"] == 7 and len(ev["examples"]) == 5
    x = ev["examples"][0]
    assert x["token_key"].startswith("solana:Top")
    assert (x["source"], x["source_field"]) == ("geckoterminal",
                                                "holders.distribution_percentage.top_10")
    assert (x["raw"], x["parse_status"]) == ("75.5", "ok")
    assert x["parsed"] == pytest.approx(0.755)
    assert x["threshold"] == {"max": 0.60}
    assert x["fetched_at_local"] != T0 - 2                 # GT's fetch, not FOMO's
    assert x["provider_timestamp"] is None
    assert book.bench_minutes("top_10_above_max") == book.bench_minutes("top_10") == 90


def test_invalid_top_10_evidence(funnel, monkeypatch):
    monkeypatch.setattr(collect, "gt_get", lambda path: gt_body("NaN"))
    monkeypatch.setattr(collect, "sol_top_wallet", lambda mint: 0.01)
    monkeypatch.setattr(main, "dossier", collect.dossier)
    rows = {tid("Bad", 0): row(tid("Bad", 0), T0 - 60 * MIN, vol=300_000)}
    funnel["now"] = list(rows)
    _, st = main.scan(Fomo(rows), None, None, 10_000.0, False, now=T0,
                      watch=Watchlist(book.DB, 72, CAP))
    x = st["evidence"]["top_10_invalid"]["examples"][0]
    assert (x["raw"], x["parse_status"], x["invalid_kind"], x["parsed"]) == \
        ("NaN", "invalid", "nan", None)


def test_capacity_scans_record_and_replay_exactly(funnel, tmp_path):
    w = full()
    rows = {tid("New", i): row(tid("New", i), T0 - 5 * MIN, vol=900_000) for i in range(33)}
    for t in first_seen(w):
        rows[t] = row(t, T0 - 10 * 3600, vol=100_000, liquidity="1")
    run = tmp_path / "run"
    run.mkdir()
    clock = session.SessionClock(T0)
    j = RecordJournal(str(run / "journal.jsonl"))
    eng = build(str(run / "paper.db"), StaticMarket(), clock=clock, journal=j)
    session.start(eng, j, clock)
    funnel["now"] = [tid("New", i) for i in range(33)]
    for i in range(2):
        ts = T0 + 900 * i
        session.poll(eng, j, clock, ts)
        session.cycle(eng, j, clock, ts + 1, lambda ask, free: main.scan(
            Fomo(rows), None, None, free, False, now=clock(), watch=w), None)
    scans = [e for e in j.events if e["kind"] == "candidates"]
    assert scans[0]["value"]["stats"]["discovery"]["eviction_operations"] == 33
    assert scans[1]["value"]["stats"]["discovery"]["eviction_operations"] == 0
    _, diffs = replay.compare_run(str(run))
    assert diffs == []
