import pytest

from eligibility import check, missing_data, policy_for
from thresholds import MISSING_DATA
from tests.helpers import answers, dossier

CHAINS = ("solana", "bsc", "base", "robinhood")


def test_complete_dossier_is_eligible_uncut():
    for chain in CHAINS:
        d = dossier(addr="0xabc" if chain != "solana" else "Mint1", chain=chain)
        v = check(d, answers(d))
        assert v.ok and v.size_factor == 1.0 and v.cuts == {}, chain


def test_every_risk_field_has_a_policy_on_every_chain():
    for name in MISSING_DATA:
        for chain in CHAINS:
            rule = policy_for(name, chain)
            assert rule in ("reject", "allow") or (rule[0] == "cut" and 0 < rule[1] <= 1)


@pytest.mark.parametrize("chain,field,expect", [
    ("solana", "mint_authority_open", "missing:mint_authority_open"),
    ("solana", "freeze_authority_open", "missing:freeze_authority_open"),
    ("bsc", "is_honeypot", "missing:is_honeypot"),
    ("base", "is_honeypot", "missing:is_honeypot"),
    ("robinhood", "holder_count", "missing:holder_count"),
    ("solana", "price_usd", "missing:price_usd"),
    ("bsc", "liquidity_usd", "missing:liquidity_usd"),
])
def test_missing_data_rejects(chain, field, expect):
    d = dossier(addr="0xabc" if chain != "solana" else "Mint1", chain=chain, **{field: None})
    assert check(d).reason == expect


@pytest.mark.parametrize("chain,fields,factor", [
    ("robinhood", ["is_honeypot", "top_10_share"], 0.4),     # dark robinhood
    ("solana", ["top_wallet_share"], 0.5),
    ("base", ["top_wallet_share"], 1.0),                      # no free source on EVM
    ("bsc", ["x_account"], 0.6),
    ("robinhood", ["is_honeypot", "x_account"], 0.3),
    ("solana", ["developer_holding_percentage"], 1.0),
])
def test_missing_data_cuts(chain, fields, factor):
    d = dossier(addr="0xabc" if chain != "solana" else "Mint1", chain=chain,
                **{f: None for f in fields})
    v = missing_data(d)
    assert v.ok and v.size_factor == factor


def test_a_missing_judge_answer_is_not_a_pass():
    d = dossier()
    a = answers(d)
    del a["concentration_is_exit_risk"]
    assert check(d, a).reason == "missing_answer:concentration_is_exit_risk"


def test_soft_gates_and_facts_still_apply():
    d = dossier()
    assert check(d, answers(d, momentum_already_spent={"noul": 0.9})).reason == \
        "momentum_already_spent"
    assert check(dossier(mint_authority_open=True)).reason == "authority_open"
    assert check(dossier(token_key=None)).reason == "no_identity"
