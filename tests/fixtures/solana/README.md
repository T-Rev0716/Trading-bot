# Solana response fixtures

**Empty on purpose.** Every file here must be a real, sanitized API response saved by
the diagnostic. Never hand-write one: the parser regression tests treat these files as
ground truth for field names, units, timestamps and authority flags.

Record them where GeckoTerminal, DexScreener and a Solana RPC are reachable:

```bash
python diagnose.py --save-fixtures tests/fixtures/solana
pytest -q tests/test_fixtures_solana.py
git add tests/fixtures/solana && git commit -m "Record Solana response fixtures"
```

Each file holds the request (no credentials), the HTTP status, the sanitized body, and
what the parser produced from it when it was recorded. A later parser change that alters
that output fails the tests until someone looks at why.

Until then `tests/test_fixtures_solana.py` is skipped, and the Solana integration stays
UNVERIFIED.
