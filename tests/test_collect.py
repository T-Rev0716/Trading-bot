import time

import collect
from collect import clean_handle, flag, share, age_minutes, normalise


def test_clean_handle():
    assert clean_handle("LuffyX100X/status/2102659581109272876") == "LuffyX100X"
    assert clean_handle("@some_coin") == "some_coin"
    assert clean_handle("https://x.com/some_coin?s=20") == "some_coin"
    assert clean_handle("https://twitter.com/abc/") == "abc"
    assert clean_handle("waytoolonghandle_123") is None
    assert clean_handle("") is None and clean_handle(None) is None


def test_flag_never_reads_no_as_true():
    assert flag("no") is False and flag("yes") is True
    assert flag(False) is False and flag(True) is True
    assert flag("unknown") is None and flag(None) is None
    assert flag("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA") is True


def test_share_and_age():
    assert share("35.5") == 0.355 and share(None) is None and share("n/a") is None
    now = time.time()
    assert round(age_minutes(now - 600)) == 10
    assert round(age_minutes((now - 600) * 1000)) == 10
    assert age_minutes(None) is None


def test_normalise():
    m = {"symbol": "T", "mcap": 1.0, "liq": 2.0, "vol24": 3.0, "price": 4.0, "holders": 0,
         "change": {300: 0.1, 3600: 0.2}, "created": time.time() - 60}
    t = normalise("abc:56", m)
    assert t["net"] == 56 and t["holder_count"] is None and t["change"]["1h"] == 0.2


class Resp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def test_trade_counts_filters_to_the_tokens_chain(monkeypatch):
    def pair(chain, liq, buys):
        return {"chainId": chain, "liquidity": {"usd": liq},
                "volume": {"h1": 10, "h6": 60},
                "txns": {"h1": {"buys": buys, "sells": 3}, "h6": {"buys": 9, "sells": 9},
                         "h24": {"buys": 100, "sells": 80}}}
    pairs = [pair("ethereum", 10**9, 1), pair("bsc", 5000, 7), pair("bsc", 1000, 2)]
    monkeypatch.setattr(collect.requests, "get", lambda *a, **k: Resp({"pairs": pairs}))
    x = collect.trade_counts({"addr": "0xabc", "net": 56, "ticker": "T"})
    assert x["buys_h1"] == 7 and x["trades_h24"] == 180 and x["volume_h6"] == 60
    monkeypatch.setattr(collect.requests, "get",
                        lambda *a, **k: Resp({"pairs": [pair("ethereum", 1, 1)]}))
    assert collect.trade_counts({"addr": "0xabc", "net": 56, "ticker": "T"})["trades_h24"] is None


def test_sol_top_wallet_skips_pools(monkeypatch):
    pool_owner, wallet, curve = "PoolPda111", "Wallet111", "Curve111"

    def rpc(method, params):
        if method == "getTokenSupply":
            return {"value": {"amount": "1000"}}
        if method == "getTokenLargestAccounts":
            return {"value": [{"address": "acct1", "amount": "400"},
                              {"address": "acct2", "amount": "200"},
                              {"address": "acct3", "amount": "30"}]}
        if method == "getMultipleAccounts" and params[1]["encoding"] == "jsonParsed":
            owners = {"acct1": pool_owner, "acct2": curve, "acct3": wallet}
            return {"value": [{"data": {"parsed": {"info": {"owner": owners[a]}}}}
                              for a in params[0]]}
        kinds = {pool_owner: {"owner": "AmmProgram"}, curve: {"owner": "PumpProgram"},
                 wallet: {"owner": collect.SYSTEM_PROGRAM}}
        return {"value": [kinds[o] for o in params[0]]}
    monkeypatch.setattr(collect, "_rpc", rpc)
    assert collect.sol_top_wallet("mint") == 0.03


def test_universe_parses_gt(monkeypatch):
    body = {"data": [{"relationships": {"base_token": {"data": {"id": "solana_Mint1"}}}},
                     {"relationships": {"base_token": {"data": {"id": "solana_Mint1"}}}},
                     {"relationships": {}}]}
    monkeypatch.setattr(collect, "gt_get", lambda path, **p: body)
    assert collect.universe(("solana",), pages=1) == ["Mint1:1399811149"]


def test_dossier_maps_gt_flags(monkeypatch):
    attrs = {"holders": {"count": 500, "distribution_percentage": {"top_10": "42.0"}},
             "mint_authority": "no", "freeze_authority": "no", "is_honeypot": "unknown",
             "twitter_handle": "coin", "gt_score_details": {"pool": 50}}
    monkeypatch.setattr(collect, "gt_get", lambda path, **p: {"data": {"attributes": attrs}})
    monkeypatch.setattr(collect, "sol_top_wallet", lambda m: 0.01)
    d = collect.dossier({"addr": "M", "net": 1399811149, "ticker": "T", "holder_count": 1})
    assert d["mint_authority_open"] is False and d["freeze_authority_open"] is False
    assert d["is_honeypot"] is None and d["top_10_share"] == 0.42
    assert d["holder_count"] == 500 and d["top_wallet_share"] == 0.01 and d["x_handle"] == "coin"
