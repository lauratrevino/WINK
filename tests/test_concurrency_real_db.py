"""
Load-tests /chat under real concurrency, against the real Postgres
instance, with threading matching how gunicorn's gthread workers actually
run multiple requests concurrently in one process.

An earlier pass on this app added an explicit "release the DB connection
before streaming" call to /chat, on the theory that Flask's
stream_with_context keeps a request's DB connection checked out of the pool
for the entire duration of a streamed response. Direct, instrumented testing
against this real Postgres instance disproved that: Flask tears down the
request/app context — which is what releases the pooled connection — as
soon as the view function returns the Response object, before the streaming
generator body (the slow part) ever runs, whether or not that explicit call
is present. It was redundant and has been removed; see wink/extensions.py's
get_db() docstring for the full explanation.

What's left of that investigation is this test: proof that a deliberately
undersized connection pool comfortably serves many concurrent
slow-streaming chat requests, because each one's connection is already back
in the pool before its "slow" part even begins — no special-case code
required, just Flask's normal request lifecycle.
"""
import threading
import time

import pytest


class SlowFakeStream:
    """Simulates a model response that takes real wall-clock time to
    stream — long enough that, if a DB connection were held for the whole
    thing, several of these running at once would starve a small pool."""
    def __init__(self, delay):
        self.delay = delay

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        time.sleep(self.delay)
        yield "Answer."


class SlowFakeMessages:
    def __init__(self, delay):
        self.delay = delay

    def stream(self, **kwargs):
        return SlowFakeStream(self.delay)


class SlowFakeClient:
    def __init__(self, delay=0.5):
        self.messages = SlowFakeMessages(delay)


@pytest.fixture()
def small_pool(app, monkeypatch):
    """Rebuild the real connection pool with only 3 connections — small
    enough that 8 concurrent slow-streaming requests would starve it if any
    of them held a connection for the duration of the stream, but large
    enough that the brief (sub-millisecond) per-request DB touches every
    request needs regardless (auth check, rate-limit check) don't cause
    contention on their own."""
    from psycopg2 import pool as pg_pool
    from psycopg2.extras import RealDictCursor
    from wink import config, extensions

    small = pg_pool.ThreadedConnectionPool(1, 3, config.DB_URL, cursor_factory=RealDictCursor)
    monkeypatch.setattr(extensions, "_db_pool", small)
    yield small
    small.closeall()


def test_many_concurrent_slow_chats_dont_starve_a_small_pool(app, client, monkeypatch, small_pool):
    import wink.blueprints.chat as chat_bp
    import wink.config as config

    monkeypatch.setattr(chat_bp, "anthropic_client", SlowFakeClient(delay=0.5))
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "fake-key-for-test")

    # Setup (register each student) happens BEFORE the timed section and
    # outside the thread pool — this isolates what's actually under test
    # (do N concurrent *chat* requests, specifically the slow-streaming
    # part, starve the pool) from unrelated contention during signup.
    N = 8
    authed_clients = []
    for i in range(N):
        c = app.test_client()
        r = c.post("/register", data={
            "email": f"loadtest{i}@utep.edu", "password": "password123",
            "first_name": "Load", "last_name": f"Test{i}",
            "classification": "Senior", "major": "Computer Science", "university": "UTEP",
        })
        assert r.status_code == 302
        authed_clients.append(c)

    results = {}
    errors = {}

    def do_chat(i, c):
        try:
            start = time.time()
            resp = c.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
            elapsed = time.time() - start
            results[i] = (resp.status_code, elapsed)
        except Exception as e:
            errors[i] = e

    threads = [threading.Thread(target=do_chat, args=(i, authed_clients[i])) for i in range(N)]
    overall_start = time.time()
    for t in threads:
        t.start()
        time.sleep(0.02)  # small, realistic stagger — not a literal single-instant burst
    for t in threads:
        t.join(timeout=15)
    overall_elapsed = time.time() - overall_start

    assert not errors, f"some requests raised exceptions: {errors}"
    assert len(results) == N, f"expected all {N} requests to complete, got {len(results)}: {results}"
    for i, (status, elapsed) in results.items():
        assert status == 200, f"request {i} failed with status {status}"

    # The real assertion: with only 3 DB connections in the pool and 8
    # concurrent 0.5s-"slow" streams, if any request held its connection
    # for the duration of its stream, requests would have to queue for a
    # free pool slot — pushing overall wall-clock time well past a single
    # stream's duration, or failing outright with a pool-exhaustion error.
    # Since each request's connection is already back in the pool (via
    # Flask's normal teardown) before its slow part starts, all 8 run that
    # slow part fully in parallel and the whole batch finishes in about
    # one stream's worth of time.
    print(f"\n{N} concurrent 0.5s-streaming chat requests against a 3-connection pool "
          f"finished in {overall_elapsed:.2f}s")
    assert overall_elapsed < 1.5, (
        f"expected all requests to run concurrently (~0.5-0.7s total); "
        f"took {overall_elapsed:.2f}s, suggesting a connection is being held "
        f"during streaming and requests are queuing for the pool"
    )
