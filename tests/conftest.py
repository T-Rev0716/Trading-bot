import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DESK_SECRET", "test-secret")
os.environ.setdefault("TYPESAFE_API_KEY", "ts-test")
os.environ["DESK_DB"] = os.path.join(tempfile.mkdtemp(), "desk.db")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def clean_book():
    import book
    import watchlist
    book.DB.executescript(watchlist.SCHEMA)
    book.DB.execute("DELETE FROM bench_v2")
    book.DB.execute("DELETE FROM watchlist")
    book.DB.commit()
    yield
