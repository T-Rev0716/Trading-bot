"""Every question the desk can ask lives here and nowhere else.

judge.py imports SETS. main.py imports STATE_FIELDS so each call only carries the
fields its questions read: accuracy falls as the state fills with unrelated material.

Units the questions rely on, fixed once in collect.py:
  change.*            fraction, 0.22 = +22%
  top_10_share        fraction of supply held by the top 10 holders, 0..1
  top_wallet_share    fraction held by the largest non-pool wallet, 0..1 (Solana only)
  mint_authority_open, freeze_authority_open, is_honeypot   true / false / null (unknown)
"""
from typesafe_sdk import Choice, Noul, Score

MARKET = {
    "shape": Choice(
        instructions="Classify the shape of this launch from the fields in `state`.",
        criteria={
            "crowd": "Holders growing faster than price. Buys outnumber sells across both "
                     "recent windows. Volume spread rather than spiking once.",
            "one_buyer": "Price climbing faster than holders. Holder growth flat while "
                         "price rises. One wallet walking the price up.",
            "fading": "Recent volume is a small fraction of the daily average, or sells "
                      "outnumber buys in both recent windows.",
            "too_early": "Too few data points to tell any of the above apart yet.",
        }),
    "liquidity_fits_ticket": Noul(
        instructions="A position of `intended_ticket_usd` could be exited into "
                     "`liquidity_usd` without moving the price more than a few percent."),
    "momentum_already_spent": Noul(
        instructions="The move in `change` has already happened, so entering now means "
                     "buying after the information is public.",
        criteria={"true": "The largest change sits in the older windows.",
                  "false": "The recent windows carry the move."}),
}

CONCENTRATION = Noul(
    instructions="Holding this token means being exit liquidity, based on "
                 "`top_10_share`, `top_wallet_share` and `holder_count`. A null field "
                 "is unknown, not safe.",
    criteria={"true": "A few wallets can end the market by selling.",
              "false": "The float is spread widely enough to absorb a large holder."})

DEV_LOADED = Noul(
    instructions="`developer_holding_percentage` is large enough that the creator "
                 "selling would meaningfully move the price. A null field is unknown, "
                 "not zero.")

CHAIN_SOLANA = {                      # 1399811149
    "authority_risk": Choice(
        instructions="Judge contract control risk from `mint_authority_open` and "
                     "`freeze_authority_open`.",
        criteria={
            "renounced": "Both false. Supply cannot be inflated, balances cannot be frozen.",
            "mint_open": "mint_authority_open is true. Supply can be inflated at will.",
            "freeze_open": "freeze_authority_open is true. Balances can be frozen at will.",
            "both_open": "Both are true.",
            "unknown": "Either field is null, so control over the contract is not known.",
        }),
    "concentration_is_exit_risk": CONCENTRATION,
    "dev_still_loaded": DEV_LOADED,
}

CHAIN_BSC = {                         # 56, same set works for Base
    "sell_side_risk": Choice(
        instructions="Judge whether a position here can be sold, from `is_honeypot`, "
                     "`gt_score_details` and the buy and sell counts.",
        criteria={
            "clean": "Not flagged, and sells are going through in the data.",
            "flagged": "Explicitly flagged as a honeypot.",
            "suspicious": "Not flagged, but sells are absent or vanishingly rare while "
                          "buys are plentiful.",
            "unknown": "The honeypot field is null and trade counts are too thin to "
                       "stand in for it.",
        }),
    "concentration_is_exit_risk": CONCENTRATION,
    "pool_quality": Score(
        instructions="Rate the pool from `gt_score_details` and `liquidity_usd`.",
        criteria=["Thin and new. One withdrawal ends the market.",
                  "Usable, but a large ticket would move it.",
                  "Deep enough that normal desk size is invisible."]),
}

CHAIN_ROBINHOOD = {                   # 4663, the one with holes in the data
    "data_coverage": Choice(
        instructions="Judge how much of this token is visible, from which fields in "
                     "`state` carry values and which are null.",
        criteria={
            "indexed": "Holder count and distribution present, honeypot field is a real "
                       "answer.",
            "partial": "Holder count present from the venue, but distribution or the "
                       "honeypot field is missing.",
            "dark": "Neither distribution nor honeypot available. Only price, volume and "
                    "pool age are known.",
        }),
    "sellable_by_evidence": Noul(
        instructions="Sells are going through on this token, judged from the buy and sell "
                     "counts rather than from any honeypot flag.",
        criteria={"true": "Sells appear across recent windows in a normal ratio.",
                  "false": "Buys with almost no sells, or no trades at all."}),
    "concentration_is_exit_risk": CONCENTRATION,
    "dev_still_loaded": DEV_LOADED,
}

SOCIAL = {
    "account_is_the_project": Noul(
        instructions="The account in `x_account` is the token's official account, not a "
                     "fan account, an impersonator, or an unrelated similar name. "
                     "`published_handle` is the handle published on chain for this token.",
        criteria={"true": "Handle matches the one published on chain and the content is "
                          "about this token.",
                  "false": "Similar name, different subject, or no link back."}),
    "audience_is_real": Noul(
        instructions="Engagement in `x_account` is consistent with its follower count, "
                     "rather than a large follower number with almost no replies or "
                     "reposts on recent posts."),
    "recycled_account": Noul(
        instructions="`x_account` shows signs of being repurposed: far older than the "
                     "token, with a handle or content history belonging to a different "
                     "project. `token.age_minutes` is the token's age."),
    "effort": Score(
        instructions="Rate how much work is visibly behind this project from `x_account`.",
        criteria=["One post, one image, nothing else.",
                  "A handful of posts, all promotional.",
                  "Regular posting with substance beyond price.",
                  "A visible team shipping visible things."]),
}


def PICK(state):
    """Options built from the shortlist at call time. Choice takes up to 255.
       Labels are unique per candidate: two launches sharing a ticker is common."""
    return {
        "best": Choice(
            instructions="Choose the single token in `candidates` that is the best entry "
                         "right now. Weigh crowd shape, contract risk, concentration and "
                         "the project account together. Prefer a clean unspent setup over "
                         "a larger move that already happened.",
            criteria={c["label"]: c["summary"] for c in state["candidates"]}),
        "worth_trading_at_all": Noul(
            instructions="At least one token in `candidates` is worth a position today, "
                         "rather than all of them being mediocre.",
            criteria={"true": "At least one is a clean setup.",
                      "false": "Every candidate has a disqualifying weakness."}),
    }


SETS = {"market": MARKET, "social": SOCIAL, "pick": PICK,
        "solana": CHAIN_SOLANA, "bsc": CHAIN_BSC, "robinhood": CHAIN_ROBINHOOD}

# the dossier fields each set reads. Nothing else goes into the state.
_FLOW = ["buys_h1", "sells_h1", "buys_h6", "sells_h6", "trades_h24"]
STATE_FIELDS = {
    "market": ["ticker", "age_minutes", "holder_count", "change", "price_usd",
               "liquidity_usd", "mcap_usd", "volume_h1", "volume_h6", "volume_h24",
               "intended_ticket_usd", *_FLOW],
    "solana": ["ticker", "mint_authority_open", "freeze_authority_open", "holder_count",
               "top_10_share", "top_wallet_share", "developer_holding_percentage"],
    "bsc": ["ticker", "is_honeypot", "gt_score_details", "holder_count", "top_10_share",
            "top_wallet_share", "liquidity_usd", *_FLOW],
    "robinhood": ["ticker", "age_minutes", "is_honeypot", "holder_count", "top_10_share",
                  "top_wallet_share", "developer_holding_percentage", "liquidity_usd",
                  "mcap_usd", "volume_h24", "price_usd", *_FLOW],
}
