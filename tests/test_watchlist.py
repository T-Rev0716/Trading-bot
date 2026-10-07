"""Discovery starvation regressions: the newest pools are too young to trade, so tokens
must be watched until they mature, re-fetched on current data, and dropped when too old."""
import sqlite3

import pytest

import book
import main
import replay
import session
from journal import RecordJournal
from market import Quote, StaticMarket
from paper import build
from tests.helpers import T0, dossier
from watchlist import Watchlist

MIN = 60
A = dossier(addr="WatchMintA111111111111111111111111111111111", ticker="AAA")
B = dossier(addr="WatchMintB111111111111111111111111111111111", ticker="BBB")
TID = {d["ticker"]: f"{d['addr']}:1399811149" for d in (A, B)}
SYMBOL = {v: k for k, v in TID.items()}


class Fomo:
    """FOMO-shaped rows for whatever is asked, with launch times set by the test."""

    def __init__(self, created):
        self.created, self.asked = created, []

    def tokens(self, ids):
        self.asked.append(list(ids))
        out = {}
        for tid in ids:
            if tid in self.created:
                out[tid] = {"symbol": SYMBOL[tid], "mcap": 400_000.0, "liq": 100_000.0,
                            "vol24": 300_000.0, "price": 1.0, "holders": 500,
                            "change": {3600: 0.1}, "created": self.created[tid]}
        return out


@pytest.fixture
def funnel(monkeypatch):
    """Real shortlist, real free/trade/chain checks; trade counts and the dossier are
       canned (complete) so a mature token reaches the candidate list."""
    pages = {"now": []}
    monkeypatch.setattr(main, "universe", lambda nets, p: list(pages["now"]))
    full = {TID["AAA"]: A, TID["BBB"]: B}
    monkeypatch.setattr(main, "trade_counts", lambda t: {
        k: full[t["tid"]][k] for k in ("buys_h1", "sells_h1", "buys_h6", "sells_h6",
                                       "trades_h24", "volume_h1", "volume_h6")})
    monkeypatch.setattr(main, "dossier", lambda t: {**full[t["tid"]], **t})
    return pages


def scan(fomo, now, watch):
    return main.scan(fomo, None, _Desk(), 10_000.0, ask_judge=False, now=now, watch=watch)


class _Desk:
    def read_x(self, handle):
        return {"handle": handle}


def wl(db=None, size=200):
    return Watchlist(db or book.DB, 72, size)


def test_young_token_matures_after_leaving_the_discovery_pages(funnel):
    watch, fomo = wl(), Fomo({TID["AAA"]: T0 - 5 * MIN, TID["BBB"]: T0 + 9 * MIN})
    funnel["now"] = [TID["AAA"]]
    cands, st = scan(fomo, T0, watch)
    assert cands == [] and st["free"] == {"age_too_young": 1}
    assert book.benched(TID["AAA"], T0) is None                 # watched, never benched
    assert watch.active(T0) == [TID["AAA"]]

    # 11 minutes later A has left the newest pages; only B is new
    funnel["now"] = [TID["BBB"]]
    cands, st = scan(fomo, T0 + 11 * MIN, watch)
    assert fomo.asked[-1] == [TID["BBB"], TID["AAA"]]           # A re-fetched, current data
    assert st["discovery"]["refetched_from_watchlist"] == 1
    assert [d["token_key"] for d, _ in cands] == [A["token_key"]]
    assert cands[0][0]["age_minutes"] == pytest.approx(16.0)    # aged on the new fetch
    assert st["free"] == {"age_too_young": 1}                   # B, still young


def test_before_the_fix_a_young_token_was_benched_and_lost(funnel):
    """documents the starvation: a 20-minute bench on a 5-minute-old token, while
       discovery moves on, meant it was never seen again"""
    watch, fomo = wl(), Fomo({TID["AAA"]: T0 - 5 * MIN})
    funnel["now"] = [TID["AAA"]]
    scan(fomo, T0, watch)
    assert book.bench_minutes("age_too_young") == 0
    funnel["now"] = []
    cands, _ = scan(fomo, T0 + 10 * MIN, watch)                 # not on any page now
    assert [d["ticker"] for d, _ in cands] == ["AAA"]


def test_watched_tokens_expire_at_max_age(funnel):
    watch = wl()
    fomo = Fomo({TID["AAA"]: T0 - (72 * 60 - 30) * MIN})        # 30 minutes to go
    funnel["now"] = [TID["AAA"]]
    scan(fomo, T0, watch)
    assert watch.active(T0) == [TID["AAA"]]
    funnel["now"] = []
    _, st = scan(fomo, T0 + 31 * MIN, watch)
    assert st["discovery"]["expired"] == 1 and watch.size() == 0
    assert fomo.asked[-1] == []                                 # never re-fetched


def test_a_token_seen_too_old_is_dropped_and_benched_for_good(funnel):
    watch = wl()
    created = {TID["AAA"]: T0 - 5 * MIN}
    fomo = Fomo(created)
    funnel["now"] = [TID["AAA"]]
    scan(fomo, T0, watch)
    created[TID["AAA"]] = T0 - 80 * 60 * MIN                    # FOMO corrects the launch
    _, st = scan(fomo, T0 + MIN, watch)
    assert st["free"] == {"age_too_old": 1} and watch.size() == 0
    assert book.benched(TID["AAA"], T0 + 1000 * MIN) == "shared"


def test_duplicate_discovery_is_one_entry_and_one_fetch(funnel):
    watch, fomo = wl(), Fomo({TID["AAA"]: T0 - 5 * MIN})
    funnel["now"] = [TID["AAA"]]
    scan(fomo, T0, watch)
    _, st = scan(fomo, T0 + 5 * MIN, watch)                     # discovered again
    assert fomo.asked[-1] == [TID["AAA"]] and st["seen"] == 1
    e = watch.entry(TID["AAA"])
    assert e["first_seen"] == T0 and e["times_seen"] == 2 and watch.size() == 1


def test_watchlist_survives_a_restart(funnel, tmp_path):
    path = str(tmp_path / "desk.db")
    fomo = Fomo({TID["AAA"]: T0 - 5 * MIN})
    funnel["now"] = [TID["AAA"]]
    first = sqlite3.connect(path)
    scan(fomo, T0, wl(first))
    first.close()                                               # the process exits
    funnel["now"] = []
    cands, st = scan(fomo, T0 + 12 * MIN, wl(sqlite3.connect(path)))
    assert st["discovery"]["refetched_from_watchlist"] == 1
    assert [d["ticker"] for d, _ in cands] == ["AAA"]


def test_watchlist_is_bounded_and_evicts_the_earliest_discovered():
    watch = wl(size=2)
    for i, tid in enumerate(["t1", "t2", "t3"]):
        watch.observe(tid, T0, T0 + i)
    assert watch.size() == 2 and watch.active(T0) == ["t2", "t3"]


def test_tokens_over_budget_are_deferred_not_lost(funnel, monkeypatch):
    monkeypatch.setattr(main, "DEX_BUDGET", 1)
    watch = wl()
    fomo = Fomo({TID["AAA"]: T0 - 30 * MIN, TID["BBB"]: T0 - 30 * MIN})
    funnel["now"] = [TID["AAA"], TID["BBB"]]
    cands, st = scan(fomo, T0, watch)
    assert len(cands) == 1 and st["deferred_budget"] == 1
    deferred = TID["BBB"] if cands[0][0]["ticker"] == "AAA" else TID["AAA"]
    assert book.benched(deferred, T0) is None and deferred in watch.active(T0)


def test_age_reasons_and_observed_ranges_are_reported(funnel):
    watch = wl()
    fomo = Fomo({TID["AAA"]: T0 - 3 * MIN, TID["BBB"]: None})
    funnel["now"] = [TID["AAA"], TID["BBB"]]
    _, st = scan(fomo, T0, watch)
    assert st["free"] == {"age_too_young": 1, "age_missing": 1}
    assert st["age"]["too_young"] == {"n": 1, "min_minutes": 3.0, "max_minutes": 3.0}
    assert st["age"]["missing"] == {"n": 1}
    assert book.benched(TID["BBB"], T0) == "shared"             # missing: short bench
    assert book.benched(TID["BBB"], T0 + 21 * MIN) is None      # then looked at again
    assert TID["BBB"] in watch.active(T0)                       # and still watched


def test_watchlist_scans_record_and_replay_exactly(funnel, tmp_path):
    """the watchlist changes what the scan returns; the journal records it, so replay
       and resume stay exact"""
    watch = wl()
    fomo = Fomo({TID["AAA"]: T0 - 5 * MIN})
    run = tmp_path / "run"
    run.mkdir()
    market = StaticMarket()
    clock = session.SessionClock(T0)
    j = RecordJournal(str(run / "journal.jsonl"))
    eng = build(str(run / "paper.db"), market, clock=clock, journal=j)
    session.start(eng, j, clock)

    def scan_fn(ask, free):
        return main.scan(fomo, None, _Desk(), free, False, now=clock(), watch=watch)
    funnel["now"] = [TID["AAA"]]
    for i in range(4):
        ts = T0 + 300 * i
        market.update(A["token_key"], Quote(1.0 + 0.01 * i, 1e5, 8e4, 3e5, ts))
        session.poll(eng, j, clock, ts)
        session.cycle(eng, j, clock, ts + 1, scan_fn, None)
        funnel["now"] = []                                     # A leaves the pages
    entries = [e for e in j.events if e["kind"] == "entries"]
    assert any(e["results"].get("baseline", {}).get("entry", {}).get("status") == "FILLED"
               for e in entries), "the matured token was never entered on paper"
    result, diffs = replay.compare_run(str(run))
    assert diffs == []
