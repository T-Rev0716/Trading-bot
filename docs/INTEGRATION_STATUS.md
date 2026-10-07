# Integration status

**Feature development is frozen.** All execution is paper-only (`venue.PaperVenue`); there
is no live order path. This page tracks whether each outside service has been checked
against **real** responses. No integration is marked verified on made-up data.

## Last diagnostic: Claude Code cloud sandbox, 2026-10-07 (Solana only)

| service | status | why |
|---|---|---|
| GeckoTerminal | **BLOCKED** | the sandbox's egress proxy refused CONNECT to `api.geckoterminal.com` (403) |
| DexScreener | **BLOCKED** | refused CONNECT to `api.dexscreener.com` (403) |
| Solana RPC | **BLOCKED** | refused CONNECT to `api.mainnet-beta.solana.com` (403) |
| Jev (TypeSafe) | **NOT CONFIGURED** | no `TYPESAFE_API_KEY` here; `api.typesafe.ai` is also refused by the proxy |
| FOMO | **NOT CONFIGURED** | optional; needs a logged-in Chrome with remote debugging. `prod-api.fomo.family` is also refused |

Consequences, stated plainly:

- **No real response has been seen.** `tests/fixtures/solana/` is empty, and
  `tests/test_fixtures_solana.py` is skipped (5 tests), which means UNVERIFIED, not passing.
- **No short real-data run has been recorded or replayed.** Replay has only been checked
  on recorded runs built from simulated quotes (`tests/test_replay.py`, `scenarios.py`).
- The diagnostic's own logic (classification, schema checks, sanitizing, fixture round
  trip) is tested with hand-written shape probes in `tests/test_diagnose.py`. Those probes
  verify the tool, not the APIs.

Raw output of `python diagnose.py` in the sandbox:

```
integration diagnostic (Solana), 2026-10-07 19:31Z - read-only, paper-only desk

geckoterminal  BLOCKED          new_pools: egress proxy refused CONNECT to api.geckoterminal.com (403); token_info: egress proxy refused CONNECT to api.geckoterminal.com (403)
  - new_pools: BLOCKED
  - token_info: BLOCKED
  setup: This environment's network policy refused the connection. Run the diagnostic on your own machine, or allow the host in your sandbox's network settings.
  setup: Locally: python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && python diagnose.py
dexscreener    BLOCKED          tokens_sample: egress proxy refused CONNECT to api.dexscreener.com (403)
  - tokens_sample: BLOCKED
  setup: This environment's network policy refused the connection. Run the diagnostic on your own machine, or allow the host in your sandbox's network settings.
  setup: Locally: python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && python diagnose.py
solana_rpc     BLOCKED          holders: egress proxy refused CONNECT to api.mainnet-beta.solana.com (403)
  - holders: BLOCKED
  setup: This environment's network policy refused the connection. Run the diagnostic on your own machine, or allow the host in your sandbox's network settings.
  setup: Locally: python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && python diagnose.py
jev            NOT_CONFIGURED   TYPESAFE_API_KEY is not set
  setup: Create a key at console.typesafe.ai -> Keys.
  setup: export TYPESAFE_API_KEY=...   (never commit it, never paste it into an issue)
  setup: python diagnose.py            (free: lists models only)
  setup: python diagnose.py --jev-call (one paid call to check the answer schema)
fomo           NOT_CONFIGURED   optional; not checked without --fomo
  setup: google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.fomo-chrome"
  setup: Log into https://fomo.family in that window and leave it open.
  setup: export CHROME_CDP=http://127.0.0.1:9222
  setup: python diagnose.py --fomo
```

## What the diagnostic verifies once it can connect

Every claim below is something the parsers rely on. Each is reported PASS, FAIL or
UNVERIFIED, with the observed value, by `python diagnose.py`. **All are UNVERIFIED today.**

| source | claim the code relies on | where it matters |
|---|---|---|
| GT new_pools | `relationships.base_token.data.id` is `solana_<mint>` | `collect.parse_new_pools` |
| GT new_pools | `pool_created_at` is ISO-8601 with an offset | age checks |
| GT token info | `mint_authority` / `freeze_authority` parse to a definite flag (`"no"`/`"yes"`, bool, or an address) | `collect.flag`; a null or `"unknown"` is reported with its raw value |
| GT token info | `holders.distribution_percentage` is in percent (buckets sum to ~100), so `top_10_share = top_10 / 100` | `collect.share`, the 60% top-10 cap |
| GT token info | `holders.count` is an integer | the 80-holder floor |
| DexScreener | `pairCreatedAt` is epoch milliseconds | pair age |
| DexScreener | `priceUsd` is a decimal string | every paper quote |
| DexScreener | `volume.h6 <= volume.h24`, the windows nest | the RISK exit ratio |
| DexScreener | `txns.*.buys/sells` are integer counts | trade cut, baseline rules |
| Solana RPC | `getTokenSupply.amount` is raw base units (`amount == uiAmount * 10^decimals`), same units as `getTokenLargestAccounts.amount` | `top_wallet_share` needs no decimals |
| Solana RPC | the largest accounts include pools/curves, owned by programs, and are skipped | `collect.sol_holders` |
| Solana RPC + GT | `top_wallet_share <= top_10_share` | cross-checks the two holder sources |
| FOMO | row fields (`marketCap`, `liquidity`, `volume24`, `holders`, `priceUSD`, `change*`, `createdAt`) present and typed | `fomo_api._row`; `change*` as fractions is checked by eye |
| Jev | a `jev*` model is listed; with `--jev-call`, every `market` answer comes back, the model id is a version, and probabilities sum to 1 | `judge.py` |

## Run it locally: exact steps

Python 3.10+, from the repository root.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

# 1. Public data, no credentials. Solana only.
python diagnose.py
python diagnose.py --save-fixtures tests/fixtures/solana   # saves sanitized responses
pytest -q tests/test_fixtures_solana.py -rs                 # must run, not skip
git add tests/fixtures/solana                               # review the files first

# A private RPC endpoint avoids public rate limits; its URL is redacted in all output.
export SOLANA_RPC_URL="https://..."                          # optional

# 2. Jev
export TYPESAFE_API_KEY=...          # never commit it, never paste it anywhere
python diagnose.py                   # free: lists models
python diagnose.py --jev-call        # one paid call: checks the answer schema

# 3. FOMO (optional for the diagnostic, required for a paper run)
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.fomo-chrome"
#    log into https://fomo.family in that window, leave it open
export CHROME_CDP=http://127.0.0.1:9222
python diagnose.py --fomo
```

Only once every row above is OK:

```bash
# 4. A short paper run on real market data, simulated execution
export DESK_SECRET="$(openssl rand -hex 24)"
uvicorn judge:app --host 127.0.0.1 --port 8080 &        # holds the Jev key
export JUDGE_URL=http://127.0.0.1:8080/judge
python main.py --nets solana --max-polls 12 --starting-cash 10000   # about an hour

# 5. Replay its journal and compare every ledger row with the run's paper.db
python replay.py --compare runs/<run_id>
python report.py runs/<run_id>
```

`replay.py --compare` exits non-zero and lists the rows if anything differs. Commit the
run directory under `runs/` only if you want it kept; `.gitignore` excludes it by default.

A paper run needs FOMO and Jev: the scan reads FOMO, and a judge failure stands the whole
cycle down. A market-data-only run without them is not supported, and building one would
be new feature work.

## Known limits of replay

- **Replay starts from the recorded scanner output.** The scan (FOMO, GeckoTerminal,
  DexScreener trade counts, Solana RPC, the per-token judge calls, the bench) is journaled
  as one `candidates` answer per cycle and is not re-executed. Replay verifies everything
  from there on, so it cannot detect a scanner or per-token judging difference.
- **The resume test covers a restart after a completed cycle**
  (`tests/test_replay.py::test_resume_continues_the_same_journal`). A crash in the middle
  of a send (an order left `SUBMITTED`) is covered by
  `tests/test_lifecycle.py::test_order_left_submitted_by_a_crash_becomes_unknown`, but not
  through a journal replay.

## Results

There are **no market results**, and this repository makes **no profitability claims**.
`examples/SYNTHETIC_RESULTS.md` holds invented scenarios that exercise the machinery; they
are not evidence, and a real paper run would only measure simulated fills.
