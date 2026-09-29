"""The optional shared bucket: one budget across replicas, never a dependency."""

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import asas_ratelimit as ratelimit


@pytest.fixture(autouse=True)
def _isolate():
    ratelimit.reset()
    ratelimit.configure(enabled=True)
    ratelimit.declare(ratelimit.Rule(name="login", limit=2, window_seconds=60))
    yield
    ratelimit.configure_shared_bucket(None)
    ratelimit.reset()
    ratelimit.configure(enabled=True)


def _run(coro):
    return asyncio.run(coro)


def test_the_shared_answer_decides_with_the_rules_own_arithmetic():
    asked = []

    async def store(key, capacity, rate):
        asked.append((key, capacity, rate))
        return False, 12.5

    ratelimit.configure_shared_bucket(store, prefix="app:dev:rl")
    assert _run(ratelimit.allow_async("login", "someone@example.com")) == (False, 12.5)
    ((key, capacity, rate),) = asked
    assert (capacity, rate) == (2, pytest.approx(2 / 60))
    assert key.startswith("app:dev:rl:login:") and "someone" not in key and "example" not in key


@pytest.mark.parametrize("failure", ["none", "raise"])
def test_no_answer_falls_back_to_the_in_process_bucket(failure):
    async def store(key, capacity, rate):
        if failure == "raise":
            raise ConnectionError("store down")
        return None

    ratelimit.configure_shared_bucket(store)
    results = [_run(ratelimit.allow_async("login", "k"))[0] for _ in range(3)]
    assert results == [True, True, False]  # still limited, per instance


def test_kill_switch_hard_block_and_unknown_rules_never_reach_the_store():
    async def store(key, capacity, rate):
        raise AssertionError("the store was asked")

    ratelimit.configure_shared_bucket(store)
    ratelimit.declare(ratelimit.Rule(name="blocked", limit=0, window_seconds=60))
    assert _run(ratelimit.allow_async("blocked", "k"))[0] is False
    assert _run(ratelimit.allow_async("nope", "k")) == (True, 0.0)
    ratelimit.configure(enabled=False)
    assert _run(ratelimit.allow_async("login", "k")) == (True, 0.0)


def test_without_a_store_the_async_path_is_the_in_process_one():
    assert [_run(ratelimit.allow_async("login", "k"))[0] for _ in range(3)] == [True, True, False]


def test_check_async_raises_429_with_retry_after():
    async def store(key, capacity, rate):
        return False, 3.2

    ratelimit.configure_shared_bucket(store)
    with pytest.raises(HTTPException) as exc:
        _run(ratelimit.check_async("login", "k"))
    assert exc.value.status_code == 429 and exc.value.headers["Retry-After"] == "4"


def _request(peer="10.0.0.9", forwarded=None):
    headers = {"x-forwarded-for": forwarded} if forwarded is not None else {}
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=peer))


def test_client_address_reads_forwarded_for_from_the_end():
    request = _request(forwarded="6.6.6.6, 1.2.3.4, 10.1.1.1")
    assert ratelimit.client_address(request, proxy_hops=1) == "10.1.1.1"
    assert ratelimit.client_address(request, proxy_hops=2) == "1.2.3.4"
    assert ratelimit.client_address(request, proxy_hops=0) == "10.0.0.9"
    assert ratelimit.client_address(_request(forwarded=""), proxy_hops=1) == "10.0.0.9"


_REDIS = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


def _redis_reachable() -> bool:
    try:
        import redis

        redis.Redis.from_url(_REDIS, socket_connect_timeout=0.3).ping()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _redis_reachable(), reason="no Redis reachable")
def test_the_redis_adapter_shares_one_bucket_between_two_clients():
    import redis
    import redis.asyncio as aredis

    prefix = f"asas-test:{uuid.uuid4().hex}"

    async def spend_from_a_new_client():
        # A fresh client per call: two calls are two "replicas".
        client = aredis.from_url(_REDIS)
        try:
            ratelimit.configure_shared_bucket(ratelimit.redis_shared_bucket(client), prefix=prefix)
            return await ratelimit.allow_async("login", "same-user")
        finally:
            await client.aclose()

    try:
        answers = [asyncio.run(spend_from_a_new_client()) for _ in range(3)]
        assert [a[0] for a in answers] == [True, True, False]
        assert 25 < answers[-1][1] <= 31  # one token per 30 s
        key = ratelimit.bucket_key("login", "same-user")
        assert 0 < redis.Redis.from_url(_REDIS).ttl(key) <= 61
    finally:
        sync = redis.Redis.from_url(_REDIS)
        for k in sync.scan_iter(f"{prefix}:*"):
            sync.delete(k)
