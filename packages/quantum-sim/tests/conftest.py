"""The one switch between "engine pending" and "engine built".

Tests that need the code of the engine change (``quantum_sim.gates``, ``quantum_sim.engine``,
``quantum_sim.domain.validate``) carry the ``engine_pending`` marker. Until that code exists
they run as strict xfails that must fail with ``NotImplementedError``: any other failure
(a wrong assertion in a test, an import error) is reported as a failure, and a test that starts
to pass fails as XPASS(strict), so the marker has to be removed from it.

``uv run pytest --runxfail`` ignores the xfail and shows the real failures. When the engine
lands, delete this hook, the marker in ``pyproject.toml`` and every ``engine_pending`` mark
(``grep -rn engine_pending tests``).
"""

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    pending = pytest.mark.xfail(
        raises=NotImplementedError, strict=True, reason="engine_pending: the engine is a stub"
    )
    for item in items:
        if item.get_closest_marker("engine_pending"):
            item.add_marker(pending)
