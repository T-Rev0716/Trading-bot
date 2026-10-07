# The desk

A memecoin desk where Jev (TypeSafe) makes every judgement, code does every number, and
Grok Bot seats size, fill and exit. Built from the "build the desk" guide, with the bugs
in it fixed (listed at the bottom).

```
0. UNIVERSE  GeckoTerminal new_pools, 3 chains          -> fresh launches
1. LIST      FOMO filterTokens, 20 per call             -> hundreds, one batch
2. FREE CUT  age, liquidity, volume, mcap. No network   -> tens
3. TRADE CUT DexScreener buys and sells, one per token  -> a handful
4. DOSSIER   GeckoTerminal info + chain RPC + X         -> four per cycle
5. JUDGE     market + chain + social per token          -> scored shortlist
6. PICK      one choice over the shortlist              -> one token, or none
```

| file              | what it is                                                       |
|-------------------|------------------------------------------------------------------|
| `judge.py`        | the only holder of the Jev key. `/judge`, `/book/held`, `/book/release` |
| `judge_client.py` | how the shift and the bots call the judge                        |
| `questions.py`    | every question the desk can ask, and the fields each set reads   |
| `collect.py`      | universe, shortlist, trade counts, dossier                       |
| `fomo_api.py`     | FOMO client, Privy bearer read out of Chrome over CDP            |
| `thresholds.py`   | every number. Retune here and nowhere else                       |
| `filter.py`       | the order the kills fire in                                      |
| `pick.py`         | CHIEF's pick and size factor                                     |
| `book.py`         | one position at a time, and the bench                            |
| `desk.py`         | the Grok Bot side: bank, X reads, shadow log, Telegram, seats    |
| `main.py`         | the shift                                                        |
| `prompts/`        | HANDOFF, SOCIAL, and SIZE / FILLS / RISK prompts for the bots    |

## Setup

Python 3.10 or newer.

```bash
pip install -r requirements.txt
cp .env.example .env        # fill it in, then: set -a; . ./.env; set +a
```

1. **Jev key.** console.typesafe.ai -> Keys. Put it in `TYPESAFE_API_KEY` on the judge
   machine only. Prove it works before anything else:

   ```bash
   curl -X POST https://api.typesafe.ai/v1/systemone \
     -H "Authorization: Bearer $TYPESAFE_API_KEY" -H "Content-Type: application/json" \
     -d '{"state":"payouts have been failing for 3 days","model":"jev-latest",
          "questions":{"urgent":{"type":"noul","instructions":"This conveys urgency"}}}'
   ```

2. **Desk secret.** `export DESK_SECRET="$(openssl rand -hex 24)"`. The bots get this,
   never the Jev key.

3. **Judge.**

   ```bash
   uvicorn judge:app --host 0.0.0.0 --port 8080
   cloudflared tunnel --url http://localhost:8080    # bots run in xAI's cloud
   ```

   Bots use `JUDGE_URL=https://<tunnel>/judge` and `BOOK_RELEASE_URL=https://<tunnel>/book/release`.
   Run the judge and the shift from the same directory (or the same `DESK_DB`) so
   `/book/release` frees the book the shift reads.

4. **FOMO session.** Start Chrome with a dedicated profile and a debugging port, log
   into fomo.family once, leave the window open:

   ```bash
   google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.fomo-chrome"
   ```

   Chrome 136+ refuses remote debugging on your default profile, hence the separate
   `--user-data-dir`. Then check the parser against the real API:

   ```bash
   python fomo_api.py probe <address>:1399811149
   ```

   The parsed row's mcap, liquidity, holders and change must match the site. If they
   do not, fix `_row` in `fomo_api.py` before anything else.

5. **Bots.** Paste `prompts/HANDOFF.md` above SCAN, VET, SOCIAL and CHIEF. Paste
   `prompts/SOCIAL.md` and `prompts/SEATS.md` into their seats. Fill in the four values
   the guide never defines (table in `prompts/SEATS.md`). Prove the judge link from a
   bot's own terminal with the curl in `prompts/HANDOFF.md`.

6. **Shadow week.**

   ```bash
   python main.py --once     # one cycle, check the log line
   python main.py            # shadow: writes shadow.jsonl, never sends, never takes the book
   ```

   Read the rows each evening and tune `thresholds.py`. Then:

   ```bash
   python main.py --live     # needs SEATS_WEBHOOK_URL so the order reaches SIZE
   ```

7. **Tests.** `pip install -r requirements-dev.txt && pytest -q`. The judge tests run
   every question set through the real `typesafe-sdk` against a mocked endpoint.

## Failure handling

| what                    | what the desk does                                            |
|-------------------------|---------------------------------------------------------------|
| GeckoTerminal 10/min    | `collect.gt_get` waits for a slot instead of earning a 429    |
| DexScreener 429         | the token reads as no pair. Lower `DEX_BUDGET` if it repeats  |
| Jev 429 / 5xx           | the SDK retries with backoff. Leave it alone                  |
| Jev 422                 | the shift stops: every token would hit the same wall          |
| judge unreachable       | the cycle stands down. No fallback to guessing                |
| dossier throws          | that token is benched 30 min. Not a pass, not a retry loop    |
| FOMO bearer expires     | refreshed at the top of every cycle by reloading the tab      |
| seats webhook fails     | the book is released, since nobody got the order              |

`python book.py` shows what is held, `python book.py release` frees it by hand.

## The bill

`cost_per_call = (state_tokens + question_tokens) / 1_000_000 * 0.042`. At most four
tokens reach the judge per cycle, three calls each plus one pick: about 13 calls of
~1,400 tokens, roughly $0.0008 a cycle, under 8 cents a day at 96 cycles.

## What changed from the guide, and why

Fixed, each with a test:

- **Solana top wallet.** The largest token account on a fresh launch is the pool or the
  bonding curve, so the guide's `top[0]` failed the 5% cap on nearly every token.
  Accounts owned by a program or a known AMM authority are now skipped.
- **GeckoTerminal flags.** GT may answer `"no"` / `"yes"` / `"unknown"` as strings. The
  guide's `mint_authority or freeze_authority` reads `"no"` as truthy and would kill
  every Solana token. All flags are normalised to `true` / `false` / `null` in
  `collect.flag`.
- **Duplicate tickers in pick.** Options were keyed by ticker, so two launches named
  `PEPE` collapsed into one. Options are now `TICKER [chain:addr6]`.
- **Single survivor skipped the size cuts.** It went out at `size_factor` 1.0 even with
  dark data or no X account. Both paths now use `pick.size_factor`.
- **Nulls passing.** `free_kill` compared `None` to numbers (a crash) and an unknown
  launch time read as age 0. Missing data now fails the hard checks.
- **DexScreener across chains.** An EVM address can exist on several chains, and the
  guide took the deepest pair on any of them. Pairs are filtered to the token's chain.
- **Honeypot on Base.** `chain_kill` only checked `chain == "bsc"`; it now applies to
  any chain that reports a honeypot flag.
- **Units.** `top_10_percent` was a percent and `top_wallet_percent` a fraction. Both
  are now fractions, renamed `top_10_share` / `top_wallet_share`.
- **Fat state.** The guide sent the whole dossier to every set despite its own rule.
  `questions.STATE_FIELDS` limits each call to the fields its questions read; a test
  checks every field a question names is actually sent.
- **Judge failures.** The guide's code skipped the token and carried on, against its
  own failure table. A 422 now stops the shift, an unreachable judge ends the cycle.
- **Judge errors.** A 401 from TypeSafe (our key) was surfaced to bots as 401, which
  reads as a bad desk secret. It is now 502.
- **RISK had no way to call `book.release()`** from xAI's cloud. `judge.py` exposes
  `POST /book/release` behind the desk secret.
- **Arithmetic.** 10 GT calls a minute minus 6 for the universe leaves 4 dossiers, not
  3 (`main.GT_DOSSIER` computes it). A $0.95 floor on $20 is 4.75% each way, 9.5% round
  trip, not 4.75% round trip.
- **Added** `authority_risk: unknown` and a `liquidity_usd` field on the order, which
  SIZE step 3 needs.

Not verifiable from here, check before going live:

- **FOMO's `/proxy/filterTokens` response shape.** It has no public docs. The parser
  assumes Codex-style rows (`token.address`, `marketCap`, `change5m`...). Run the probe.
- **Privy storage key.** The bearer is read from `localStorage['privy:token']`.
- **DexScreener's chainId for Robinhood.** Set to `robinhood` in `collect.DEX_CHAIN`.
  If every Robinhood token dies as `no_pair`, the log will say which chain ids it saw.
- **X reads and seat delivery.** The guide leaves how the shift reaches Grok Bot open.
  `desk.py` uses `X_READER_URL` and `SEATS_WEBHOOK_URL`. Without an X reader every
  token carries the no-social 0.60 cut.
- **The four undefined seat numbers** in `prompts/SEATS.md`.

This trades real money in very risky assets. Run the shadow week.
