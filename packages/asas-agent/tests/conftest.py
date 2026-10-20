"""Standalone package fixtures. No database, no network, no model: the release tests
read a fixture agents directory, and the eval tests drive fake attempts and a fake
inner model."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def agents_dir() -> Path:
    return FIXTURES / "agents"


def run(coro):
    """Drive a coroutine from a sync test without an async plugin."""
    return asyncio.run(coro)
