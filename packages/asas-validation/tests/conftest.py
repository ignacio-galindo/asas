"""Package fixtures. The engine is stateless except for four registries — declared
rules, known fields, the clock, and host-registered kinds. Each test declares what it
needs; the autouse fixture resets all four so tests never leak into each other (a
module-level seam without a matching teardown silently changes every later test)."""

import pytest

from asas_validation import catalog, clock, configure, declare_rules, engine


@pytest.fixture(autouse=True)
def _reset_registries():
    yield
    declare_rules(())
    catalog._FIELDS.clear()
    configure()
    engine._KINDS.clear()
    engine._KINDS.update(engine._BUILTIN_KINDS)
    assert clock.today is not None  # module still importable after reset
