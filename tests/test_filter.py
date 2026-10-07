from filter import free_kill, trade_kill, chain_kill, soft_kill


def tok(**kw):
    t = {"age_minutes": 60, "liquidity_usd": 50_000, "volume_h24": 100_000,
         "mcap_usd": 500_000}
    t.update(kw)
    return t


def test_free_kill_passes_a_clean_token():
    assert free_kill(tok()) is None


def test_free_kill_never_lets_null_pass():
    assert free_kill(tok(age_minutes=None)) == "age_missing"
    assert free_kill(tok(liquidity_usd=None)) == "liquidity_missing"
    assert free_kill(tok(volume_h24=None)) == "volume_missing"
    assert free_kill(tok(mcap_usd=None)) == "mcap_missing"


def test_free_kill_bounds():
    assert free_kill(tok(age_minutes=5)) == "age_too_young"
    assert free_kill(tok(age_minutes=14.99)) == "age_too_young"
    assert free_kill(tok(age_minutes=15)) is None                  # the floor is inclusive
    assert free_kill(tok(age_minutes=72 * 60)) is None             # so is the ceiling
    assert free_kill(tok(age_minutes=73 * 60)) == "age_too_old"
    assert free_kill(tok(mcap_usd=9_000_000)) == "mcap_above_max"
    assert free_kill(tok(liquidity_usd=11_999)) == "liquidity_below_min"


def test_trade_kill():
    assert trade_kill({"trades_h24": None, "sells_h1": None, "buys_h1": None}) == "no_pair"
    assert trade_kill({"trades_h24": 100, "sells_h1": 5, "buys_h1": 5}) == "trades"
    assert trade_kill({"trades_h24": 500, "sells_h1": 0, "buys_h1": 40}) == "no_sells"
    assert trade_kill({"trades_h24": 500, "sells_h1": 10, "buys_h1": 40}) is None


def dos(**kw):
    d = {"chain": "solana", "top_wallet_share": 0.02, "top_10_share": 0.3,
         "holder_count": 400, "mint_authority_open": False,
         "freeze_authority_open": False, "is_honeypot": None}
    d.update(kw)
    return d


def test_chain_kill_facts():
    assert chain_kill(dos()) is None
    assert chain_kill(dos(top_wallet_share=0.08)) == "top_wallet"
    assert chain_kill(dos(top_10_share=0.7)) == "top_10"
    assert chain_kill(dos(holder_count=10)) == "holders"
    assert chain_kill(dos(mint_authority_open=True)) == "authority_open"
    assert chain_kill(dos(freeze_authority_open=None)) is None     # unknown -> the judge
    assert chain_kill(dos(chain="bsc", is_honeypot=True)) == "honeypot"
    assert chain_kill(dos(chain="base", is_honeypot=True)) == "honeypot"


def answers(**kw):
    a = {"shape": {"type": "choice", "choice": "crowd", "confidence": 0.8,
                   "probabilities": {"crowd": 0.8, "one_buyer": 0.1, "fading": 0.05,
                                     "too_early": 0.05}},
         "liquidity_fits_ticket": {"type": "noul", "noul": 0.9},
         "momentum_already_spent": {"type": "noul", "noul": 0.2},
         "concentration_is_exit_risk": {"type": "noul", "noul": 0.2}}
    a.update(kw)
    return a


def test_soft_kill():
    assert soft_kill(answers()) is None
    assert soft_kill(answers(concentration_is_exit_risk={"noul": 0.6})) == \
        "concentration_is_exit_risk"
    assert soft_kill(answers(effort={"type": "score", "score": 0.4})) == "effort"
    weak = answers()
    weak["shape"] = dict(weak["shape"], probabilities={"crowd": 0.5})
    assert soft_kill(weak) == "shape_weak"
    fading = answers()
    fading["shape"] = dict(fading["shape"], choice="fading")
    assert soft_kill(fading) == "shape"
    assert soft_kill(answers(sell_side_risk={"choice": "suspicious"})) == "sell_side"
