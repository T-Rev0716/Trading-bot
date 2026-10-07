from sizing import entry_fee, intended_ticket, ticket

CFG = {"kelly_fraction": 0.03, "max_ticket_fraction": 0.06, "max_pool_share": 0.02,
       "max_fee_rate": 0.01}


def test_kelly_times_free_cash():
    assert ticket(10_000, 1.0, 1_000_000, cfg=CFG) == (300.0, None)


def test_kelly_is_clamped_at_the_max_fraction():
    assert ticket(10_000, 1.0, 1_000_000, cfg=CFG | {"kelly_fraction": 0.5})[0] == 600.0


def test_size_factor_and_pool_cap():
    assert ticket(10_000, 0.6, 1_000_000, cfg=CFG)[0] == 180.0
    assert ticket(10_000, 1.0, 5_000, cfg=CFG)[0] == 100.0        # 2% of a 5k pool


def test_fee_floor_returns_zero():
    # 3% of 1,500 = 45; fee 0.95 is 2.1% of 45 > 1% max
    assert ticket(1_500, 1.0, 1_000_000, cfg=CFG) == (0.0, "fee_floor")
    assert ticket(10_000, 0.3, 1_000_000, cfg=CFG) == (0.0, "fee_floor")    # $90 ticket


def test_never_sizes_without_cash_or_liquidity():
    assert ticket(0, 1.0, 1e6, cfg=CFG) == (0.0, "no_free_cash")
    assert ticket(1e4, 1.0, None, cfg=CFG) == (0.0, "no_liquidity")
    assert ticket(1e4, 0.0, 1e6, cfg=CFG) == (0.0, "size_factor_zero")


def test_rounds_down_to_cents_and_fee_math():
    assert ticket(10_001.99, 1.0, 1e9, cfg=CFG)[0] == 300.05
    assert entry_fee(100) == 0.95 and entry_fee(1000) == 4.5
    assert intended_ticket(1_000) == 60.0


def test_default_bank_can_clear_the_fee_floor():
    """With the guide's $1,500 bank no ticket could ever clear a 1% fee: 6% of 1,500 is
       $90 and the $0.95 floor needs $95. The paper default must be able to trade."""
    from thresholds import PAPER, SIZING
    assert ticket(1_500, 1.0, 1e9, cfg=SIZING | {"kelly_fraction": 1.0})[1] == "fee_floor"
    assert ticket(PAPER["starting_cash_usd"], 1.0, 1e9)[1] is None
