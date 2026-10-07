import pytest

from ids import label, token_key


def test_evm_keys_are_lowercase_and_chain_scoped():
    assert token_key("bsc", "0xAbC") == "bsc:0xabc"
    assert token_key("bsc", "0xabc") != token_key("base", "0xabc")


def test_solana_keys_keep_case():
    assert token_key("solana", "MiNt") == "solana:MiNt" != token_key("solana", "mint")


def test_label_carries_the_full_address():
    addr = "So1anaMint1111111111111111111111111111111111"
    assert label({"ticker": "PEPE", "token_key": token_key("solana", addr)}) == \
        f"PEPE (solana:{addr})"


def test_bad_identity_is_refused():
    with pytest.raises(ValueError):
        token_key("eth", "0xabc")
    with pytest.raises(ValueError):
        token_key("bsc", "")
