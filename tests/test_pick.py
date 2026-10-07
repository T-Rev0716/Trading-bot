"""One survivor or many: the same checks, the same gates, the same size cuts."""
import pytest

import cycle
import pick
from eligibility import check
from ids import label
from tests.helpers import answers, dossier, engines, pick_judge, quote

A = "So1anaMintAAAAAA111111111111111111111111111"
B = "So1anaMintAAAAAA222222222222222222222222222"     # same 16-char prefix as A


def elig(d, **ov):
    a = answers(d, **ov)
    return (d, a, check(d, a))


def test_single_survivor_must_pass_the_absolute_gate():
    # regression: a lone survivor used to bypass worth_trading_at_all entirely
    one = [elig(dossier(addr=A))]
    assert pick.select(pick_judge(worth=0.4), one)[0] is None
    chosen, info = pick.select(pick_judge(worth=0.9), one)
    assert chosen[0]["addr"] == A and info["confidence"] == 1.0


def test_many_survivors_pass_the_same_gates():
    two = [elig(dossier(addr=A)), elig(dossier(addr=B))]
    assert pick.select(pick_judge(worth=0.4), two)[0] is None
    assert pick.select(pick_judge(conf=0.5), two)[0] is None


def test_same_ticker_same_prefix_resolves_by_full_address():
    da, db = dossier(addr=A, ticker="PEPE"), dossier(addr=B, ticker="PEPE")
    assert label(da) != label(db)
    chosen, info = pick.select(pick_judge(choose=label(db)), [elig(da), elig(db)])
    assert chosen[0]["token_key"] == f"solana:{B}"
    assert info["runner_up"][0][0] == label(da)


def test_duplicate_identity_is_refused():
    d = dossier(addr=A)
    with pytest.raises(ValueError):
        pick.select(pick_judge(), [elig(d), elig(dict(d))])


@pytest.mark.parametrize("n", [1, 2])
def test_size_cuts_apply_alone_or_among_many(n):
    """regression: a lone survivor went out at size_factor 1.0 with no X account"""
    eng, market, _ = engines(names=("strategy",))
    ds = [dossier(addr=A, x_account=None, x_handle=None),
          dossier(addr=B, x_account=None, x_handle=None)][:n]
    for d in ds:
        market.update(d["token_key"], quote())
    out = cycle.enter([(d, answers(d)) for d in ds], pick_judge(), eng, "c1")
    assert out["strategy"]["entry"]["status"] == "FILLED"
    o = eng["strategy"].ledger.orders("FILLED")[0]
    import json
    assert json.loads(o["meta"])["size_factor"] == 0.6


def test_candidates_without_answers_never_reach_the_strategy():
    eng, market, _ = engines()
    d = dossier(addr=A)
    market.update(d["token_key"], quote())
    calls = []
    out = cycle.enter([(d, None)], pick_judge(calls=calls), eng, "c1")
    assert calls == [] and out["strategy"]["note"] == "no eligible candidate"
    assert out["baseline"]["entry"]["status"] == "FILLED"
