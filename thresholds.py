"""Every number the desk tunes. When you retune, you edit this file and nothing else."""

HARD = {                        # stage 2, arithmetic, runs before anything costs money
    "min_age_minutes":   15,    # younger than this and the data is noise
    "max_age_hours":     72,    # older than this and it is not a launch any more
    "min_liquidity_usd": 12_000,
    "min_volume_h24":    40_000,
    "min_mcap_usd":      60_000,
    "max_mcap_usd":      8_000_000,
    "min_trades_h24":    150,
    "max_top_wallet":    0.05,  # solana only, exact, from RPC, pools excluded
    "max_top_10":        0.60,  # where distribution exists
    "min_holders":       80,
}

SOFT = {                        # applied to Jev's answers, per token
    "concentration_is_exit_risk": ("max", 0.55),
    "momentum_already_spent":     ("max", 0.60),
    "liquidity_fits_ticket":      ("min", 0.60),
    "account_is_the_project":     ("min", 0.70),
    "recycled_account":           ("max", 0.50),
    "audience_is_real":           ("min", 0.45),
    "effort":                     ("min", 1.0),    # expected score, 0..3
    "dev_still_loaded":           ("max", 0.55),
    "sellable_by_evidence":       ("min", 0.60),   # robinhood
}

SHAPE_MIN_CROWD = 0.55          # probabilities["crowd"], not the winning label
PICK_MIN_WORTH  = 0.60          # absolute gate, applied to one survivor or many
PICK_MIN_CONF   = 0.55          # relative gate. One option is chosen with certainty 1.0

# What a null risk field does to a candidate, per chain ("*" is every other chain).
#   "reject"         the candidate is not eligible
#   ("cut", f)       eligible, ticket multiplied by f. Cuts stack.
#   "allow"          eligible, no change. Used only where the chain has no source at all.
# Applied identically to the Jev strategy and the rules-only baseline.
MISSING_DATA = {
    "price_usd":             {"*": "reject"},
    "liquidity_usd":         {"*": "reject"},
    "trades_h24":            {"*": "reject"},
    "holder_count":          {"*": "reject"},
    "top_10_share":          {"robinhood": ("cut", 0.80), "*": ("cut", 0.50)},
    "top_wallet_share":      {"solana": ("cut", 0.50), "*": "allow"},   # free only on Solana
    "mint_authority_open":   {"solana": "reject", "*": "allow"},        # EVM: n/a
    "freeze_authority_open": {"solana": "reject", "*": "allow"},
    "is_honeypot":           {"solana": "allow", "robinhood": ("cut", 0.50), "*": "reject"},
    "x_account":             {"*": ("cut", 0.60)},                     # no X read
    "developer_holding_percentage": {"*": "allow"},                    # the judge sees null
}

# Rules-only baseline: the same candidates and checks as the strategy, no judge.
BASELINE = {
    "min_buy_share_h1": 0.55,   # buys / (buys + sells), last hour: the crowd proxy
    "min_buy_share_h6": 0.50,
    "min_h1_volume_share": 1 / 48,   # h1 volume at least half the hourly 24h average
    "max_change_1h": 1.00,      # +100% in the last hour reads as momentum already spent
}

# Paper engine. Live execution is disabled: these are the only fills there are.
PAPER = {
    # Default paper bank; override with --starting-cash or PAPER_STARTING_CASH. At the
    # guide's $1,500 no ticket clears the fee limit (6% of 1,500 is $90, and a $0.95
    # floor is 1% only from $95 up), so that bank never trades. See scenarios.py.
    "starting_cash_usd": 10_000.0,
    "fee_rate": 0.0045,         # FOMO fee, per side
    "min_fee_usd": 0.95,        # FOMO fee floor, per side
    # slippage_bps = base + impact * (notional / liquidity_usd * 100). In a constant-
    # product pool, 1% of total liquidity is 2% of one reserve, roughly 200 bps.
    "base_slippage_bps": 50,
    "impact_bps_per_pct_of_pool": 200,
    "max_slippage_bps": 500,    # entries expected over this are rejected; exits complete
                                # and are flagged
}

SIZING = {
    # A FIXED PLACEHOLDER, not a Kelly allocation and not an estimate of anything. No edge
    # has been measured, so there is nothing to compute a Kelly fraction from. Every
    # ticket is this share of free cash, before the cuts and caps below.
    "fixed_ticket_fraction": 0.03,
    "max_ticket_fraction": 0.06,  # of free cash. Free cash only, never the locked bag
    "max_pool_share": 0.02,     # more than 2% of the pool and you are the exit
    "max_fee_rate": 0.01,       # one side's fee over 1% of the ticket: do not trade
}

EXITS = {
    "min_volume_ratio": 0.20,   # volume_h6 / (volume_h24 / 4) under this: close
    "quote_retries": 2,         # a failed quote is retried twice, then the position closes
    "stale_quote_haircut": 0.25,  # ASSUMPTION: a blind close is valued at last price *
                                  # (1 - this). Reported separately, with a 0% stress.
    "stop_loss": None,          # e.g. 0.30 closes at -30%. None: rule off
    "take_profit": None,        # e.g. 1.00 closes at +100%. None: rule off
    "max_hold_minutes": None,   # None: rule off
}

RECONCILE = {
    # An order the venue has no record of only counts as never received after this long.
    # Before that, UNKNOWN stays UNKNOWN and nothing new is sent.
    "unknown_grace_seconds": 120,
}
