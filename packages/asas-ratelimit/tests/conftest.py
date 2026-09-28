"""Standalone package fixtures.

Store: process memory always; Redis when a server answers at
``TEST_REDIS_URL`` (default ``redis://localhost:6379/0``) and redis-py is
installed. Unreachable or not installed, the Redis legs skip, the way the
Postgres-only tiers skip on SQLite elsewhere in the repo. Every run writes
under its own key prefix and deletes it afterwards, so a shared developer
Redis is left as it was found.
"""

import os
import uuid

import pytest

import asas_ratelimit as ratelimit

_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/0")
_RUN_PREFIX = f"asas-ratelimit-test:{uuid.uuid4().hex}:"


def _probe_redis():
    try:
        import redis
    except ImportError:
        return None, "redis-py is not installed (the [redis] extra)"
    client = redis.Redis.from_url(_REDIS_URL, socket_connect_timeout=0.5, socket_timeout=5)
    try:
        client.ping()
    except redis.exceptions.RedisError as exc:
        return None, f"no Redis at {_REDIS_URL}: {exc}"
    return client, ""


_REDIS_CLIENT, _REDIS_SKIP_REASON = _probe_redis()

requires_redis = pytest.mark.skipif(_REDIS_CLIENT is None, reason=_REDIS_SKIP_REASON)


def _leftover_keys():
    return list(_REDIS_CLIENT.scan_iter(match=_RUN_PREFIX + "*"))


@pytest.fixture()
def redis_client():
    if _REDIS_CLIENT is None:
        pytest.skip(_REDIS_SKIP_REASON)
    yield _REDIS_CLIENT
    for k in _leftover_keys():
        _REDIS_CLIENT.delete(k)


@pytest.fixture()
def redis_prefix(redis_client):
    """A prefix unique to this test, under the run's prefix."""
    return f"{_RUN_PREFIX}{uuid.uuid4().hex[:8]}:"


@pytest.fixture()
def make_redis_store(redis_client, redis_prefix):
    """Build RedisStores on the test prefix; each can stand in for a replica."""
    import redis

    def make(*, client=None, prefix=redis_prefix, **kwargs):
        if client is None:
            client = redis.Redis.from_url(_REDIS_URL, socket_timeout=5)
        return ratelimit.RedisStore(client, prefix=prefix, **kwargs)

    return make


@pytest.fixture(params=["memory", pytest.param("redis", marks=requires_redis)])
def store(request):
    """The store-contract fixture: every test using it runs on each store."""
    if request.param == "memory":
        return ratelimit.MemoryStore()
    return request.getfixturevalue("make_redis_store")()


def pytest_sessionfinish(session, exitstatus):
    """Belt and braces: nothing from this run survives in Redis."""
    if _REDIS_CLIENT is not None:
        for k in _leftover_keys():
            _REDIS_CLIENT.delete(k)
