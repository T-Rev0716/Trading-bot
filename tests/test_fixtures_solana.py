"""Parser regression tests against REAL Solana responses in tests/fixtures/solana.

Skipped until those fixtures are recorded with `python diagnose.py --save-fixtures
tests/fixtures/solana`. A skip here means the Solana parsers are UNVERIFIED, not passing.
"""
import os

import pytest

from tests import fixture_checks
from tests.conftest import ROOT

FIX = os.path.join(ROOT, "tests", "fixtures", "solana")
DOCS = fixture_checks.load_all(FIX)
pytestmark = pytest.mark.skipif(
    not DOCS, reason="UNVERIFIED: no real Solana fixtures recorded yet. Run "
                     "`python diagnose.py --save-fixtures tests/fixtures/solana` where "
                     "the APIs are reachable.")


@pytest.mark.parametrize("check", fixture_checks.ALL, ids=lambda c: c.__name__)
def test_parsers_reproduce_recorded_output(check):
    check(DOCS)
