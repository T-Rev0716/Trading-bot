import book


def test_take_held_release():
    assert book.held() is None
    book.take({"token": {"ticker": "T", "address": "a", "network_id": 56}})
    assert book.held()["ticker"] == "T"
    book.release()
    assert book.held() is None


def test_bench_by_reason():
    book.sit("a:1", "honeypot")
    book.sit("b:1", "age")
    assert book.benched("a:1") and book.benched("b:1")
    assert not book.benched("c:1")
    book.DB.execute("UPDATE bench SET until = 0 WHERE tid='b:1'")
    assert not book.benched("b:1")
