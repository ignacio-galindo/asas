"""Bucket stores: the contract every store meets (run on memory and Redis),
then what only a shared store has to prove: atomicity across threads and
processes' worth of clients, key expiry, prefix isolation, the server clock,
and the fail-open/fail-closed policy when Redis cannot answer."""

import logging
import threading
import time
import uuid

import pytest
from fastapi import HTTPException

import asas_ratelimit as ratelimit

from conftest import requires_redis


@pytest.fixture(autouse=True)
def _clean_engine():
    """Each test starts on the default store, no rules, defaults restored."""
    ratelimit.configure(enabled=True, clock=None, store=None)
    ratelimit.reset()
    yield
    # Restore the default store first: resetting a dead-Redis store raises.
    ratelimit.configure(enabled=True, clock=None, store=None)
    ratelimit.reset()


def _use(store):
    ratelimit.configure(store=store)
    return store


# ── the store contract: identical answers on every store ────────────────────


def test_contract_depletes_then_denies_with_retry_after(store):
    _use(store)
    ratelimit.declare(ratelimit.Rule(name="c.basic", limit=3, window_seconds=3600))
    key = uuid.uuid4().hex
    assert all(ratelimit.allow("c.basic", key)[0] for _ in range(3))
    allowed, retry = ratelimit.allow("c.basic", key)
    assert not allowed
    assert 1190 < retry <= 1200  # one token = 3600/3 seconds


def test_contract_refills_over_time(store):
    _use(store)
    ratelimit.declare(ratelimit.Rule(name="c.refill", limit=20, window_seconds=1))
    key = uuid.uuid4().hex
    assert all(ratelimit.allow("c.refill", key)[0] for _ in range(20))
    assert not ratelimit.allow("c.refill", key)[0]
    time.sleep(0.15)  # 20/s refills a token every 50ms
    assert ratelimit.allow("c.refill", key)[0]


def test_contract_burst_caps_the_bucket(store):
    _use(store)
    ratelimit.declare(ratelimit.Rule(name="c.burst", limit=60, window_seconds=3600, burst=2))
    key = uuid.uuid4().hex
    assert ratelimit.allow("c.burst", key)[0]
    assert ratelimit.allow("c.burst", key)[0]
    assert not ratelimit.allow("c.burst", key)[0]


def test_contract_keys_and_rules_are_independent(store):
    _use(store)
    ratelimit.declare(ratelimit.Rule(name="c.a", limit=1, window_seconds=3600))
    ratelimit.declare(ratelimit.Rule(name="c.b", limit=1, window_seconds=3600))
    assert ratelimit.allow("c.a", "alice")[0]
    assert not ratelimit.allow("c.a", "alice")[0]
    assert ratelimit.allow("c.a", "bob")[0]
    assert ratelimit.allow("c.b", "alice")[0]


def test_contract_hard_block_and_zero_burst_deny(store):
    _use(store)
    ratelimit.declare(ratelimit.Rule(name="c.blocked", limit=0, window_seconds=60))
    ratelimit.declare(ratelimit.Rule(name="c.noburst", limit=5, window_seconds=60, burst=0))
    assert ratelimit.allow("c.blocked", "k") == (False, 60)
    assert not ratelimit.allow("c.noburst", "k")[0]
    with pytest.raises(HTTPException) as exc:
        ratelimit.check("c.blocked", "k")
    assert exc.value.status_code == 429


def test_contract_kill_switch_and_unknown_rule_allow(store):
    _use(store)
    ratelimit.declare(ratelimit.Rule(name="c.off", limit=1, window_seconds=3600))
    key = uuid.uuid4().hex
    ratelimit.check("c.off", key)
    ratelimit.configure(enabled=False)
    for _ in range(5):
        ratelimit.check("c.off", key)  # would 429 if enforced
    ratelimit.configure(enabled=True)  # back on: the same store, still spent
    assert ratelimit._store is store
    assert not ratelimit.allow("c.off", key)[0]
    assert ratelimit.allow("c.never_declared", key)[0]


def test_contract_check_raises_429_with_retry_after(store):
    _use(store)
    ratelimit.declare(ratelimit.Rule(name="c.raise", limit=1, window_seconds=60))
    key = uuid.uuid4().hex
    ratelimit.check("c.raise", key)
    with pytest.raises(HTTPException) as exc:
        ratelimit.check("c.raise", key)
    assert exc.value.status_code == 429
    assert int(exc.value.headers["Retry-After"]) == 60


def test_contract_clear_counters_keeps_rules(store):
    _use(store)
    ratelimit.declare(ratelimit.Rule(name="c.clear", limit=1, window_seconds=3600))
    key = uuid.uuid4().hex
    assert ratelimit.allow("c.clear", key)[0]
    assert not ratelimit.allow("c.clear", key)[0]
    ratelimit.clear_counters()
    assert "c.clear" in ratelimit.rules()
    assert ratelimit.allow("c.clear", key)[0]


def test_contract_threads_never_overspend(store):
    """Atomicity: 16 threads racing for 50 tokens get exactly 50."""
    _use(store)
    ratelimit.declare(ratelimit.Rule(name="c.race", limit=50, window_seconds=3600))
    key = uuid.uuid4().hex
    results = []
    barrier = threading.Barrier(16)

    def worker():
        barrier.wait()
        results.extend(ratelimit.allow("c.race", key)[0] for _ in range(20))

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 320
    assert sum(results) == 50


# ── wiring ──────────────────────────────────────────────────────────────────


def test_default_store_is_memory_and_none_restores_it():
    assert isinstance(ratelimit._store, ratelimit.MemoryStore)
    other = ratelimit.MemoryStore()
    ratelimit.configure(store=other)
    assert ratelimit._store is other
    ratelimit.configure(store=None)
    assert ratelimit._store is ratelimit._DEFAULT_STORE


def test_configure_rejects_a_non_store_at_boot():
    with pytest.raises(TypeError):
        ratelimit.configure(store=object())
    assert isinstance(ratelimit._store, ratelimit.MemoryStore)


def test_a_custom_store_satisfies_the_protocol():
    class Deny:
        def take(self, rule, key):
            return False, 7.0

        def clear(self):
            pass

    ratelimit.configure(store=Deny())
    ratelimit.declare(ratelimit.Rule(name="c.custom", limit=5, window_seconds=60))
    assert ratelimit.allow("c.custom", "k") == (False, 7.0)


def test_redis_store_rejects_an_empty_prefix(redis_client):
    with pytest.raises(ValueError):
        ratelimit.RedisStore(redis_client, prefix="")


# ── Redis: shared state, atomicity across clients, expiry, prefix, clock ────


@requires_redis
def test_redis_replicas_share_one_bucket(make_redis_store):
    """Two stores on two clients stand in for two replicas: together they
    get the limit once, not twice (the N-times-looser problem, fixed)."""
    rule = ratelimit.Rule(name="r.shared", limit=4, window_seconds=3600)
    a, b = make_redis_store(), make_redis_store()
    key = uuid.uuid4().hex
    outcomes = [(a if i % 2 else b).take(rule, key)[0] for i in range(8)]
    assert sum(outcomes) == 4


@requires_redis
def test_redis_atomic_across_many_clients(make_redis_store):
    """Every thread gets its own client and store (its own connection), so
    this is the multi-process race: no read-modify-write overspend."""
    rule = ratelimit.Rule(name="r.race", limit=37, window_seconds=3600)
    stores = [make_redis_store() for _ in range(12)]
    key = uuid.uuid4().hex
    wins = []
    barrier = threading.Barrier(len(stores))

    def worker(s):
        barrier.wait()
        wins.extend(s.take(rule, key)[0] for _ in range(10))

    threads = [threading.Thread(target=worker, args=(s,)) for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 120
    assert sum(wins) == 37


@requires_redis
def test_redis_ttl_is_the_time_to_refill(make_redis_store, redis_client):
    store = make_redis_store()
    rule = ratelimit.Rule(name="r.ttl", limit=3, window_seconds=60)
    key = uuid.uuid4().hex
    store.take(rule, key)  # 2 of 3 left: one token (20s) short of full
    assert 19_000 < redis_client.pttl(store.key(rule.name, key)) <= 20_000
    store.take(rule, key)
    store.take(rule, key)  # empty: a full 60s to refill
    assert 59_000 < redis_client.pttl(store.key(rule.name, key)) <= 60_000


@requires_redis
def test_redis_idle_bucket_expires(make_redis_store, redis_client):
    store = make_redis_store()
    rule = ratelimit.Rule(name="r.expire", limit=10, window_seconds=0.2)
    key = uuid.uuid4().hex
    for _ in range(10):
        store.take(rule, key)
    assert redis_client.exists(store.key(rule.name, key))
    time.sleep(0.35)
    assert not redis_client.exists(store.key(rule.name, key))
    assert store.take(rule, key)[0]  # an expired bucket is a full one


@requires_redis
def test_redis_prefix_namespaces_keys_and_clear(make_redis_store, redis_client, redis_prefix):
    rule = ratelimit.Rule(name="r.prefix", limit=1, window_seconds=3600)
    # Glob metacharacters in the prefix must be matched literally by clear():
    # unescaped, "x[ab]*:" would also match the neighbour's "xa..." keys.
    mine = make_redis_store(prefix=redis_prefix + "x[ab]*:")
    neighbour = make_redis_store(prefix=redis_prefix + "xa:")
    assert mine.take(rule, "k")[0]
    assert neighbour.take(rule, "k")[0]  # a different prefix is a different bucket
    assert not mine.take(rule, "k")[0]
    assert mine.key(rule.name, "k").startswith(redis_prefix + "x[ab]*:")
    mine.clear()
    assert not redis_client.exists(mine.key(rule.name, "k"))
    assert redis_client.exists(neighbour.key(rule.name, "k"))


@requires_redis
def test_redis_key_is_unambiguous_across_rule_names(make_redis_store):
    store = make_redis_store()
    assert store.key("a:b", "c") != store.key("a", "b:c")


@requires_redis
def test_redis_ignores_the_local_clock(make_redis_store):
    """The Redis store reads the server's TIME: a replica whose clock runs
    ahead (or an injected one) cannot mint tokens."""
    _use(make_redis_store())
    ratelimit.declare(ratelimit.Rule(name="r.clock", limit=2, window_seconds=3600))
    key = uuid.uuid4().hex
    ratelimit.configure(clock=lambda: 0.0)
    assert ratelimit.allow("r.clock", key)[0]
    assert ratelimit.allow("r.clock", key)[0]
    ratelimit.configure(clock=lambda: 1e12)  # "a year later", locally
    assert not ratelimit.allow("r.clock", key)[0]


# ── Redis: failure policy ────────────────────────────────────────────────────


def _dead_client():
    redis = pytest.importorskip("redis")
    # Port 1 refuses connections; nothing to wait for.
    return redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2, socket_timeout=0.2)


def test_redis_down_fails_open_by_default_with_one_warning(caplog):
    store = _use(ratelimit.RedisStore(_dead_client()))
    assert store.fail_open is True
    ratelimit.declare(ratelimit.Rule(name="f.open", limit=1, window_seconds=60))
    with caplog.at_level(logging.WARNING, logger="asas_ratelimit.redis_store"):
        for _ in range(3):
            assert ratelimit.allow("f.open", "k") == (True, 0.0)
            ratelimit.check("f.open", "k")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1  # once per outage, not once per request
    assert "failing open" in warnings[0].getMessage()


def test_redis_down_fails_closed_when_asked(caplog):
    _use(ratelimit.RedisStore(_dead_client(), fail_open=False))
    ratelimit.declare(ratelimit.Rule(name="f.closed", limit=30, window_seconds=3600))
    with caplog.at_level(logging.WARNING, logger="asas_ratelimit.redis_store"):
        assert ratelimit.allow("f.closed", "k") == (False, 120.0)  # one token interval
        with pytest.raises(HTTPException) as exc:
            ratelimit.check("f.closed", "k")
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "120"
    assert "failing closed" in caplog.records[0].getMessage()


def test_redis_down_keeps_hard_block_and_kill_switch():
    """Both are the engine's, decided before the store is asked."""
    _use(ratelimit.RedisStore(_dead_client(), fail_open=True))
    ratelimit.declare(ratelimit.Rule(name="f.blocked", limit=0, window_seconds=60))
    assert ratelimit.allow("f.blocked", "k") == (False, 60)
    _use(ratelimit.RedisStore(_dead_client(), fail_open=False))
    ratelimit.declare(ratelimit.Rule(name="f.any", limit=5, window_seconds=60))
    ratelimit.configure(enabled=False)
    assert ratelimit.allow("f.any", "k") == (True, 0.0)


@requires_redis
def test_redis_outage_then_recovery_enforces_again(make_redis_store, redis_client, caplog):
    import redis

    class Flaky:
        """The real client, with a switch that makes every script call fail."""

        down = False

        def register_script(self, source):
            real = redis_client.register_script(source)

            def call(keys, args):
                if Flaky.down:
                    raise redis.exceptions.ConnectionError("simulated outage")
                return real(keys=keys, args=args)

            return call

        def __getattr__(self, name):
            return getattr(redis_client, name)

    store = make_redis_store(client=Flaky())
    rule = ratelimit.Rule(name="f.flaky", limit=1, window_seconds=3600)
    key = uuid.uuid4().hex
    assert store.take(rule, key)[0]
    Flaky.down = True
    with caplog.at_level(logging.INFO, logger="asas_ratelimit.redis_store"):
        assert store.take(rule, key)[0]  # fail-open during the outage
        Flaky.down = False
        assert not store.take(rule, key)[0]  # back: the bucket was kept, still spent
    messages = [r.getMessage() for r in caplog.records]
    assert any("unreachable" in m for m in messages)
    assert any("reachable again" in m for m in messages)
