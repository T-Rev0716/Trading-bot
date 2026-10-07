import pytest

from pick import pick, size_factor, label


def surv(ticker, addr, chain="solana", **ans):
    d = {"ticker": ticker, "addr": addr, "net": 1399811149, "chain": chain,
         "age_minutes": 42.4, "mcap_usd": 300_000, "liquidity_usd": 50_000,
         "holder_count": 300}
    a = {"shape": {"choice": "crowd", "probabilities": {"crowd": 0.8}},
         "concentration_is_exit_risk": {"noul": 0.1},
         "account_is_the_project": {"noul": 0.9}, "effort": {"score": 2.0}}
    a.update(ans)
    return d, a


def fake_judge(choice, conf=0.8, worth=0.9):
    calls = []

    def judge(qs, state):
        calls.append((qs, state))
        labels = [c["label"] for c in state["candidates"]]
        probs = {l: (conf if l == choice else (1 - conf) / (len(labels) - 1)) for l in labels}
        return {"model": "jev-1.13.0", "answers": {
            "best": {"type": "choice", "choice": choice, "confidence": conf,
                     "probabilities": probs},
            "worth_trading_at_all": {"type": "noul", "noul": worth}}}
    judge.calls = calls
    return judge


def test_duplicate_tickers_resolve_to_the_right_token():
    s = [surv("PEPE", "aaaaaaa1"), surv("PEPE", "bbbbbbb2")]
    o = pick(fake_judge(label(s[1][0])), s)
    assert o["token"]["address"] == "bbbbbbb2"
    assert o["runner_up"][0][0] == label(s[0][0])


def test_pick_stands_down():
    s = [surv("A", "aaaaaa"), surv("B", "bbbbbb")]
    assert pick(fake_judge(label(s[0][0]), worth=0.5), s) is None
    assert pick(fake_judge(label(s[0][0]), conf=0.5), s) is None


def test_single_survivor_is_refused():
    with pytest.raises(ValueError):
        pick(fake_judge("x"), [surv("A", "aaaaaa")])


def test_size_factor_stacks():
    assert size_factor({"account_is_the_project": {}}) == 1.0
    assert size_factor({}) == 0.6
    assert size_factor({"data_coverage": {"choice": "dark"}}) == 0.24
