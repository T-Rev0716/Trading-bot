# The desk (paper only)

A memecoin desk where Jev (TypeSafe) makes the judgements and Python does every number,
every size, every fill and every exit, **on paper**. Live execution is disabled: there
is no code path to a real venue, and `python main.py --live` exits with an error.

Two ledgers trade side by side on the same candidates, the same simulated venue, the
same sizing and the same exits:

- **strategy**: Jev's answers, the SOFT gates, the pick.
- **baseline**: rules only, no judge. If the strategy cannot beat this, the judge is not
  adding anything.

```
0. UNIVERSE  GeckoTerminal new_pools, 3 chains          -> fresh launches
1. LIST      FOMO filterTokens, 20 per call             -> hundreds, one batch
2. FREE CUT  age, liquidity, volume, mcap. No network   -> tens
3. TRADE CUT DexScreener buys and sells, one per token  -> a handful
4. DOSSIER   GeckoTerminal info + chain RPC + X         -> four per cycle
5. ELIGIBLE  facts + missing-data policy (both ledgers) -> candidates
6. JUDGE     market + chain + social per token          -> strategy only
7. SELECT    strategy: pick gates   baseline: rules     -> one token each, or none
8. PAPER     size, simulated fill, poll, exit           -> ledgers, report
```

| file              | what it is                                                        |
|-------------------|-------------------------------------------------------------------|
| `judge.py`        | the only holder of the Jev key. `/judge` only                     |
| `judge_client.py` | how the shift calls the judge                                     |
| `questions.py`    | every question the desk can ask, and the fields each set reads    |
| `collect.py`      | universe, shortlist, trade counts, dossier                        |
| `fomo_api.py`     | FOMO client, Privy bearer read out of Chrome over CDP             |
| `ids.py`          | token identity: `chain:full_address`, never a ticker              |
| `thresholds.py`   | every number, including missing-data, sizing, exit and paper settings |
| `filter.py`       | the funnel's kills, in order                                      |
| `eligibility.py`  | the one eligibility check every candidate goes through            |
| `pick.py`         | the strategy's selection and its gates                            |
| `baseline.py`     | the rules-only selection                                          |
| `cycle.py`        | entries for one cycle, shared by the shift and replay             |
| `sizing.py`       | SIZE, enforced in Python                                          |
| `exits.py`        | RISK, enforced in Python                                          |
| `broker.py`       | the broker interface: `submit` and `lookup`                       |
| `venue.py`        | `PaperVenue`, the only broker: deterministic fills, fees, slippage, faults |
| `ledger.py`       | cash, orders, positions, marks; the order lifecycle               |
| `paper.py`        | the engine tying ledger, broker and market together               |
| `market.py`       | quotes: live DexScreener, and a static market for tests/scenarios |
| `journal.py`      | the ordered event record, and strict replay of it                 |
| `session.py`      | poll and cycle, the same code for a live run and a replay         |
| `report.py`       | performance per ledger: observed, assumed and stress, side by side |
| `replay.py`       | exact replay of a run's journal, failing on the first divergence  |
| `scenarios.py`    | SYNTHETIC scenarios, kept apart from any market evidence          |
| `diagnose.py`     | read-only integration diagnostic; saves sanitized fixtures        |
| `sanitize.py`     | strips keys, auth headers, cookies and login tokens from output   |
| `book.py`         | the bench, scoped shared or strategy-only                         |
| `desk.py`         | X reads and Telegram reports. No order delivery                   |
| `main.py`         | the paper shift                                                   |
| `prompts/`        | the bot prompts, kept for reference. Not wired to anything        |

## Setup

Python 3.10 or newer.

```bash
pip install -r requirements.txt
cp .env.example .env        # fill it in, then: set -a; . ./.env; set +a
```

0. **Check the integrations first:** `python diagnose.py` (see
   `docs/INTEGRATION_STATUS.md`). Do not run the desk until every service is OK.
1. **Jev key** in `TYPESAFE_API_KEY` on the judge machine. Prove it with the curl in
   `prompts/HANDOFF.md`.
2. **Desk secret:** `export DESK_SECRET="$(openssl rand -hex 24)"`.
3. **Judge:** `uvicorn judge:app --host 127.0.0.1 --port 8080`, and
   `JUDGE_URL=http://127.0.0.1:8080/judge` for the shift.
4. **FOMO session:** start Chrome with `--remote-debugging-port=9222
   --user-data-dir="$HOME/.fomo-chrome"`, log into fomo.family, then check the parser:
   `python fomo_api.py probe <address>:1399811149`.
5. **Run on paper:** `python main.py --once`, then `python main.py`. Each run gets its
   own `runs/<run_id>/` with `paper.db` and `journal.jsonl`. It polls every 5 minutes
   and scans every 15. `--starting-cash 1500` (or `PAPER_STARTING_CASH`) sets the paper
   bank for a new run; `--resume runs/<run_id>` continues one after a crash.
6. **Read it:** `python report.py runs/<run_id>` for both ledgers.
   `python replay.py runs/<run_id>/journal.jsonl` replays the run and confirms it
   reproduces exactly.
7. **Tests:** `pip install -r requirements-dev.txt && pytest -q`.

## The paper engine

**Fills** (`venue.simulate_fill`) are a pure function of side, reference price, pool
liquidity and size:

```
slippage_bps = base_slippage_bps + impact_bps_per_pct_of_pool * (size / liquidity * 100)
buy:   price = ref * (1 + slip);  spends the ticket on tokens, fee on top
sell:  price = ref * (1 - slip);  sells the whole position, fee out of proceeds
fee    = max(0.45% * notional, $0.95) per side
```

An **entry** whose expected slippage is over `max_slippage_bps` is rejected: the engine
checks before sending (`slippage_over_max`, nothing reaches the venue) and the venue
refuses one anyway. An **exit** over the maximum completes and is flagged, because a
close is not optional. No price or no liquidity: the venue rejects.

**Sizing** (`sizing.ticket`), the four SIZE steps: `fixed_ticket_fraction` of free cash,
clamped at 6%; times the size factor; at most 2% of the pool; zero if one side's fee is
over 1% of the ticket. Free cash is the ledger's cash minus whatever unsettled buys
reserved. **The 3% is a fixed placeholder, not a Kelly allocation**: Kelly needs a
measured edge and there is none, so nothing is being estimated. Change it only on the
strength of paper results.

**Starting cash** is set per run (`--starting-cash`, `PAPER_STARTING_CASH`, default
$10,000) and fixed for that run's life; reopening a ledger with a different amount is
refused. At $1,500 nothing trades: 3% is $45, the 6% cap is $90, and the $0.95 fee
only falls to 1% at $95.

**Exits** (`exits.decide`), polled every 5 minutes:
- `volume_h6 / (volume_h24 / 4) < 0.20` closes.
- No quote after two retries closes **blind**. Its value (last price minus a 25%
  haircut) is an **assumption**, not an observation, and the report keeps it apart:
  `realized_pnl_observed` vs `realized_pnl_assumed`, plus a **zero-recovery stress**
  that values every blind close and every open position at $0, as if unsellable.
- Missing or zero volume closes: a position you cannot measure is one you do not hold.
- Optional stop-loss, take-profit and max hold, all off by default.

**Order lifecycle** (`ledger.py`):

```
NEW -> SUBMITTED -> FILLED | REJECTED | UNKNOWN
UNKNOWN -> FILLED | REJECTED     only through reconciliation against the venue's record
```

- Every order has a unique idempotency key (`ledger:buy:chain:address:cycle` or
  `ledger:sell:position:attempt`) and an id derived from it; a position's id is derived
  from the buy that opened it. A repeat is refused. The venue never fills the same order
  id twice. Derived ids are what let a replay be compared row for row.
- A delivery timeout is **UNKNOWN, never failed**. The buy's cash stays reserved, a
  closing position stays `CLOSING`, and that ledger refuses every new order until
  reconciliation settles it. The venue saying "never received" only counts after a
  120 s grace period. A venue that cannot be asked keeps the order UNKNOWN indefinitely.
- An order found `SUBMITTED` at startup (a crash mid-send) is treated as UNKNOWN. An order
  still `NEW` was never handed over and is rejected as `never_sent`.
- A position records the buy that opened it. It can only be released by a filled sell
  created for that same position, on the same token and ledger. Anything else raises
  `ReleaseMismatch`. A rejected close puts the position back to `OPEN`.
- One position per ledger at a time. While both ledgers hold, the shift does not scan.

**Eligibility** (`eligibility.check`) is the same for one survivor or many: facts, then
the missing-data policy, then (strategy only) every required judge answer and the SOFT
gates. The strategy's pick then applies `worth_trading_at_all >= 0.60` whether there is
one candidate or ten. With one candidate the choice question is not asked, because a
choice over one option is certain by construction (confidence 1.0), so the absolute gate
decides, exactly as it does for many.

**Missing risk data** (`thresholds.MISSING_DATA`): every risk field has a written rule
per chain: `reject`, `("cut", f)` or `allow`. The rules are conservative defaults, so edit them
deliberately:

| field | solana | bsc / base | robinhood |
|---|---|---|---|
| price, liquidity, trades, holder_count | reject | reject | reject |
| mint / freeze authority | reject | allow (n/a on EVM) | allow |
| is_honeypot | allow (n/a) | reject | cut 0.5 |
| top_10_share | cut 0.5 | cut 0.5 | cut 0.8 |
| top_wallet_share | cut 0.5 | allow (no free source) | allow |
| x_account | cut 0.6 | cut 0.6 | cut 0.6 |
| developer_holding_percentage | allow | allow | allow |

Cuts stack: a dark Robinhood token with no X account trades at 0.5 × 0.8 × 0.6 = 0.24.

**Identity** is `chain:full_address` everywhere (EVM addresses lowercased, Solana kept
case-sensitive). Pick options read `TICKER (chain:address)`.

## Broker interface

`broker.Broker` is two calls: `submit(order, ref_price, liquidity, flags) -> Fill` and
`lookup(order_id) -> (FILLED | REJECTED | NOT_FOUND, ...)`, with `BrokerReject`,
`DeliveryTimeout` and `LookupUnavailable` as the only ways they fail. **`PaperVenue` is
the only implementation.** The engine refuses any broker that is not a journaled
`PaperVenue`, and a test fails if another class in the repository implements both calls.

## Journal and exact replay

Every run writes `journal.jsonl`: one event per line, numbered in the order it happened.

| event | what |
|---|---|
| `session`, `resume` | config snapshot, starting cash, start time |
| `poll`, `cycle` | an operation and its timestamp (time is frozen per operation) |
| `quote` | every quote attempt: the quote, or the error |
| `submit`, `lookup` | every broker call: request, fill, reject, timeout, outage |
| `candidates`, `judge` | the scan's result, the pick's answer |
| `reconciled`, `exits`, `entries`, `equity`, `cycle_error` | what the desk decided, and cash and equity after |

`replay.py` rebuilds empty ledgers and runs the **same** poll and cycle code
(`session.py`) with the journal in place of the outside world: each call is answered with
the next recorded answer, each decision is compared with the next recorded one. Nothing is
looked up by time or by token, so a decision cannot see a quote from after it. A changed
quote, a reordered event, a missing event or a changed threshold stops the replay with
`ReplayDivergence` and the sequence number. It is a fidelity check, not a what-if tool.

The regression tests record runs with quote outages, a lost-before-receipt entry,
executed-but-unacknowledged entries and exits, venue lookups that fail, a blind close and
a restart. Each replay must reproduce the orders, order history, positions, balances and
equity marks exactly. `python replay.py --compare runs/<run_id>` does the same for a real
run: it replays the journal and compares every ledger row with the run's `paper.db`.

Two limits:
- **Replay starts from the recorded scanner output.** The scan (FOMO, GeckoTerminal,
  DexScreener, Solana RPC, the per-token judge calls, the bench) is one journaled
  `candidates` answer per cycle and is not re-run, so replay cannot catch a scanner or
  per-token judging difference.
- **The resume test covers a restart after a completed cycle.** A crash mid-send (an
  order left `SUBMITTED`) is covered by a ledger unit test, not by a journal replay.

## Results

### Market evidence

**None.** No integration has been reached from the build environment (see
`docs/INTEGRATION_STATUS.md`), so nothing here has run on live data, and this repository
makes no profitability claims. A paper run on live data would still be simulated fills,
not trading results.

### Synthetic demonstrations (not evidence)

`python scenarios.py` runs invented scenarios at a $1,500 and a $10,000 bank, writes
`examples/SYNTHETIC_RESULTS.md`, and writes replayable journals to `examples/synthetic/`.
The scenarios are built to exercise the machinery: the strategy/baseline split (the
`trap` scenario is **rigged** so the judge looks right), an unsellable token with the
zero-recovery stress, and a lost acknowledgement. They say nothing about edge.

## Integration status

**Feature development is frozen until the integrations are checked against real
responses.** `python diagnose.py` checks each service on its own (read-only, Solana
only), reports connectivity, schema, missing fields and parse errors, and verifies the
field meanings the parsers rely on. It never prints, logs or saves keys, authorization
headers, cookies or login tokens. Details, the last run's output and exact local setup
steps: **`docs/INTEGRATION_STATUS.md`**.

| integration | status | evidence |
|---|---|---|
| GeckoTerminal | **BLOCKED** here | proxy refused; no real response seen. Parsers unverified |
| DexScreener | **BLOCKED** here | proxy refused; no real response seen. Robinhood chainId still a guess |
| Solana RPC | **BLOCKED** here | proxy refused; holder math tested on fabricated accounts only |
| Jev (TypeSafe) API | **NOT CONFIGURED** here | no key; the SDK is exercised against a mocked endpoint only |
| FOMO + Privy bearer | **NOT CONFIGURED** here | no logged-in Chrome; response shape is an assumption |
| X reads (SOCIAL) | **unfinished** | `X_READER_URL` has nothing behind it |
| Telegram reports | **unexercised** | standard Bot API call, never run |
| Paper engine, ledger, journal, replay | **tested** | deterministic; record-then-replay matches every ledger row. Not yet run on a live-data journal |
| Live order execution | **disabled** | no live path exists |

## What changed from the guide, and why

- **Paper only.** Orders go to `venue.PaperVenue`. The SIZE/FILLS/RISK prompts are now
  Python (`sizing.py`, `venue.py`, `exits.py`); the prompts are reference only.
- **The guide's bank cannot trade.** At $1,500, 6% is $90 and the $0.95 fee floor is
  only 1% from $95. The paper bank defaults to $10,000 and is configurable per run.
- **Single survivors** used to skip both the pick gates and the size cuts. Now they go
  through the same checks as everyone else.
- **Missing data** is a written policy per field and chain, not an implicit pass.
- **Identity** is the full chain:address. Pick labels used to be ticker plus 6 chars.
- **Solana top wallet** skips pools and bonding curves, which otherwise failed every
  token. **GeckoTerminal flags** read `"no"` as false. **DexScreener pairs** are
  filtered to the token's chain. **Honeypot** applies on every chain that reports it.
- **Fat state:** each judge call only carries the fields its questions read.
- **Judge failures** stand the cycle down; a 422 stops the shift.
- **Arithmetic:** 10 GT calls minus 6 leaves 4 dossiers. A $0.95 fee on $20 is 4.75%
  per side, 9.5% round trip.

This is a research tool for very risky assets. Paper results with simulated fills will
look better than real ones: real pools move between quote and fill, and some honeypots
only show on the sell.
