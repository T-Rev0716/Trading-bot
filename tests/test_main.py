import json

import pytest

import book
import main
from judge_client import JudgeUnavailable


def fomo_token(tid, ticker):
    addr, net = tid.split(":")
    return {"addr": addr, "net": int(net), "tid": tid, "ticker": ticker,
            "mcap_usd": 400_000, "liquidity_usd": 60_000, "volume_h24": 300_000,
            "price_usd": 0.001, "holder_count": 500, "change": {"1h": 0.1},
            "age_minutes": 90}


class Desk:
    def __init__(self, x=None):
        self.shadow, self.x = [], x

    def read_x(self, handle):
        return self.x

    def log_shadow(self, order, stats):
        json.dumps(order, default=str)
        self.shadow.append(order)


GOOD = {
    "market": {"shape": {"type": "choice", "choice": "crowd", "confidence": 0.8,
                         "probabilities": {"crowd": 0.8, "one_buyer": 0.1, "fading": 0.05,
                                           "too_early": 0.05}},
               "liquidity_fits_ticket": {"type": "noul", "noul": 0.9},
               "momentum_already_spent": {"type": "noul", "noul": 0.2}},
    "solana": {"authority_risk": {"type": "choice", "choice": "renounced", "confidence": 0.9,
                                  "probabilities": {"renounced": 0.9}},
               "concentration_is_exit_risk": {"type": "noul", "noul": 0.1},
               "dev_still_loaded": {"type": "noul", "noul": 0.1}},
    "social": {"account_is_the_project": {"type": "noul", "noul": 0.95},
               "audience_is_real": {"type": "noul", "noul": 0.8},
               "recycled_account": {"type": "noul", "noul": 0.05},
               "effort": {"type": "score", "score": 2.0}},
}


@pytest.fixture
def wired(monkeypatch):
    toks = [fomo_token("Mint1:1399811149", "AAA"), fomo_token("Mint2:1399811149", "AAA"),
            dict(fomo_token("Mint3:1399811149", "OLD"), age_minutes=10_000)]
    monkeypatch.setattr(main, "universe", lambda nets, pages: [t["tid"] for t in toks])
    monkeypatch.setattr(main, "shortlist", lambda fomo, ids: [dict(t) for t in toks])
    monkeypatch.setattr(main, "trade_counts", lambda t: {
        "buys_h1": 50, "sells_h1": 30, "buys_h6": 300, "sells_h6": 200, "trades_h24": 900,
        "volume_h1": 1, "volume_h6": 6})
    monkeypatch.setattr(main, "dossier", lambda t: {
        **t, "chain": "solana", "top_10_share": 0.3, "top_wallet_share": 0.02,
        "mint_authority_open": False, "freeze_authority_open": False, "is_honeypot": None,
        "developer_holding_percentage": None, "gt_score_details": None,
        "description": "d", "x_handle": "coin"})
    return toks


def make_judge(winner_index=0, log=None):
    def judge(qs, state):
        if log is not None:
            log.append((qs, state))
        if qs == "pick":
            labels = [c["label"] for c in state["candidates"]]
            return {"model": "jev-1.13.0", "answers": {
                "best": {"choice": labels[winner_index], "confidence": 0.8,
                         "probabilities": {l: 0.8 if i == winner_index else 0.2
                                           for i, l in enumerate(labels)}},
                "worth_trading_at_all": {"noul": 0.9}}}
        return {"model": "jev-1.13.0", "answers": GOOD[qs]}
    return judge


def test_shadow_cycle_picks_logs_and_never_takes_the_book(wired):
    desk, log = Desk(x={"handle": "coin"}), []
    order, stats = main.run_once(None, make_judge(1, log), desk, bank=1000, shadow=True)
    assert order is None and book.held() is None
    assert stats["free"] == {"age": 1} and stats["survivors"] == 2
    assert desk.shadow[0]["token"]["address"] == "Mint2"       # same ticker, right token
    assert desk.shadow[0]["size_factor"] == 1.0
    assert book.benched("Mint3:1399811149")
    market_state = next(s for q, s in log if q == "market")
    assert market_state["intended_ticket_usd"] == 60.0
    assert "description" not in market_state and "x_account" not in market_state


def test_live_cycle_takes_the_book_and_next_cycle_does_not_scan(wired):
    order, _ = main.run_once(None, make_judge(), Desk(), bank=1000, shadow=False)
    assert order["size_factor"] == 0.6                          # no X account
    assert book.held()["ticker"] == "AAA"
    order2, stats = main.run_once(None, make_judge(), Desk(), bank=1000, shadow=False)
    assert order2 is None and stats["held"] == "AAA"


def test_single_survivor_skips_pick_but_keeps_size_cuts(wired, monkeypatch):
    log = []
    wired[1]["age_minutes"] = 5                                 # killed by age
    order, stats = main.run_once(None, make_judge(log=log), Desk(), bank=1000, shadow=False)
    assert order["confidence"] is None and order["size_factor"] == 0.6
    assert all(q != "pick" for q, _ in log)


def test_judge_down_stands_the_cycle_down(wired):
    def judge(qs, state):
        raise JudgeUnavailable("down")
    with pytest.raises(JudgeUnavailable):
        main.run_once(None, judge, Desk(), bank=1000, shadow=False)
    assert book.held() is None
