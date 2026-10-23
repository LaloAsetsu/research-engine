"""The import contracts of pyproject.toml must fail on the imports they exist to stop.

Each case copies the package to a temporary folder, adds one import and runs import-linter
there. A contract that stopped working would pass `lint-imports` forever; these cases catch that.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
RUN = "from importlinter.cli import lint_imports_command; lint_imports_command()"

LAYERS = "Hexagonal layers: outer layers may import inner ones, never the reverse"
INDEPENDENT = "Adapters are independent of each other"
PURE = "Domain is pure: no framework, driver or sibling-package imports"
PORTS_ONLY = "Application and ports reach docingest, quantum-sim and qa-bench only through ports"
DOCINGEST = "Only the docingest adapters import docingest"
QUANTUM = "Only the circuits adapter imports quantum_sim"
QA_BENCH = "Only the bench adapter imports qa_bench"
SQL = "Only the persistence adapter imports sqlalchemy and alembic"
PROTECTED = "Only the composition root and entrypoints wire concrete adapters"
PLUGIN = "research_api never imports a docingest plugin (docingest finds it by entry point)"

SRC = "src/research_api"

# The application imports the domain, so a forbidden import in the domain also breaks the
# application's contract through the chain; the cases below list both.

# (file, line added, contracts that must break). An empty set is a control: the import is allowed.
# A file that does not exist yet is created, which is how a new adapter is simulated.
CASES = {
    "domain imports an adapter": (
        "domain/status.py",
        "from ..adapters.clock import SystemClock",
        {LAYERS, PROTECTED},
    ),
    "ports import the application": (
        "ports/clock.py",
        "from ..application import status",
        {LAYERS},
    ),
    "domain imports a web framework": ("domain/status.py", "import fastapi", {PURE, PORTS_ONLY}),
    "domain imports sqlalchemy": ("domain/status.py", "import sqlalchemy", {PURE, PORTS_ONLY, SQL}),
    "domain imports docingest": (
        "domain/status.py",
        "import docingest",
        {PURE, PORTS_ONLY, DOCINGEST},
    ),
    "domain imports jsonschema": ("domain/status.py", "import jsonschema", {PURE, PORTS_ONLY}),
    "application imports httpx": ("application/status.py", "import httpx", {PORTS_ONLY}),
    "ports import quantum_sim": ("ports/clock.py", "import quantum_sim", {PORTS_ONLY, QUANTUM}),
    "application imports an adapter": (
        "application/status.py",
        "from ..adapters import fake",
        {LAYERS, PROTECTED},
    ),
    "the clock adapter imports docingest": (
        "adapters/clock/__init__.py",
        "import docingest",
        {DOCINGEST},
    ),
    "the persistence adapter imports docingest": (
        "adapters/persistence/__init__.py",
        "import docingest",
        {DOCINGEST},
    ),
    "the docingest adapter imports quantum_sim": (
        "adapters/docingest_ask/__init__.py",
        "import quantum_sim",
        {QUANTUM},
    ),
    "the circuits adapter imports qa_bench": (
        "adapters/circuits/__init__.py",
        "import qa_bench",
        {QA_BENCH},
    ),
    "the hashing adapter imports sqlalchemy": (
        "adapters/hashing/__init__.py",
        "import sqlalchemy",
        {SQL},
    ),
    "the files adapter imports alembic": ("adapters/files/__init__.py", "import alembic", {SQL}),
    "bootstrap imports docingest itself": (
        "bootstrap/container.py",
        "import docingest",
        {DOCINGEST},
    ),
    "an entrypoint imports qa_bench itself": ("entrypoints/cli.py", "import qa_bench", {QA_BENCH}),
    "one adapter imports another": (
        "adapters/bench/__init__.py",
        "from .. import files",
        {INDEPENDENT},
    ),
    "an adapter imports a plugin": (
        "adapters/circuits/__init__.py",
        "import docingest_index",
        {PLUGIN},
    ),
    "bootstrap imports a plugin": ("bootstrap/container.py", "import docingest_index", {PLUGIN}),
    "the package root imports docingest": ("__init__.py", "import docingest", {DOCINGEST}),
    "the adapters package imports sqlalchemy": (
        "adapters/__init__.py",
        "import sqlalchemy",
        {SQL},
    ),
    "a new adapter package imports docingest": (
        "adapters/auth/__init__.py",
        "import docingest",
        {DOCINGEST},
    ),
    "a new adapter module imports sqlalchemy": (
        "adapters/sessions.py",
        "import sqlalchemy",
        {SQL},
    ),
    "a new adapter module imports quantum_sim": (
        "adapters/sessions.py",
        "import quantum_sim",
        {QUANTUM},
    ),
    "a new adapter module imports qa_bench": (
        "adapters/reports.py",
        "import qa_bench",
        {QA_BENCH},
    ),
    "a module inside the docingest adapter imports quantum_sim": (
        "adapters/docingest_ask/parsing.py",
        "import quantum_sim",
        {QUANTUM},
    ),
    "the fake adapter imports the clock adapter": (
        "adapters/fake/__init__.py",
        "from .. import clock",
        {INDEPENDENT},
    ),
    "the circuit use cases import quantum_sim": (
        "application/circuits.py",
        "import quantum_sim",
        {PORTS_ONLY, QUANTUM},
    ),
    "the circuit rules import docingest": (
        "domain/circuits.py",
        "import docingest",
        {PURE, PORTS_ONLY, DOCINGEST},
    ),
    "the circuit port imports quantum_sim": (
        "ports/circuits.py",
        "import quantum_sim",
        {PORTS_ONLY, QUANTUM},
    ),
    "the circuits adapter imports another adapter": (
        "adapters/circuits/lane.py",
        "from .. import files",
        {INDEPENDENT},
    ),
    "control: the lane inside the circuits adapter imports quantum_sim": (
        "adapters/circuits/lane.py",
        "import quantum_sim\nimport docingest",
        set(),
    ),
    "control: a module inside the docingest adapter imports docingest": (
        "adapters/docingest_ask/parsing.py",
        "import docingest",
        set(),
    ),
    "control: a module inside persistence imports sqlalchemy": (
        "adapters/persistence/models.py",
        "import sqlalchemy",
        set(),
    ),
    "control: docingest in its adapter": (
        "adapters/docingest_ask/__init__.py",
        "import docingest",
        set(),
    ),
    "control: docingest in the circuits adapter": (
        "adapters/circuits/__init__.py",
        "import docingest",
        set(),
    ),
    "control: quantum_sim in the circuits adapter": (
        "adapters/circuits/__init__.py",
        "import quantum_sim",
        set(),
    ),
    "control: qa_bench in the bench adapter": (
        "adapters/bench/__init__.py",
        "import qa_bench",
        set(),
    ),
    "control: sqlalchemy in persistence": (
        "adapters/persistence/__init__.py",
        "import sqlalchemy\nimport alembic",
        set(),
    ),
    "control: bootstrap imports an adapter": (
        "bootstrap/container.py",
        "from ..adapters import persistence",
        set(),
    ),
    "control: an entrypoint imports bootstrap": (
        "entrypoints/cli.py",
        "from .. import bootstrap",
        set(),
    ),
}


def lint(root: Path) -> tuple[int, str]:
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "NO_COLOR": "1"}
    done = subprocess.run(
        [sys.executable, "-c", RUN],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode, " ".join((done.stdout + done.stderr).split())


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    shutil.copy(PACKAGE / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copytree(PACKAGE / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


def test_the_package_keeps_every_contract(sandbox: Path) -> None:
    code, out = lint(sandbox)
    assert code == 0, out
    assert " BROKEN" not in out


@pytest.mark.parametrize("case", CASES)
def test_a_contract_breaks_on_the_import_it_forbids(sandbox: Path, case: str) -> None:
    file, line, expected = CASES[case]
    target = sandbox / SRC / file
    target.parent.mkdir(parents=True, exist_ok=True)
    before = target.read_text(encoding="utf-8") if target.exists() else ""
    target.write_text(before + "\n" + line + "\n", encoding="utf-8")
    code, out = lint(sandbox)
    broken = {
        name
        for name in (
            LAYERS,
            INDEPENDENT,
            PURE,
            PORTS_ONLY,
            DOCINGEST,
            QUANTUM,
            QA_BENCH,
            SQL,
            PROTECTED,
            PLUGIN,
        )
        if f"{name} BROKEN" in out
    }
    assert broken == expected, out
    assert (code != 0) == bool(expected)


def adapter_areas(adapters: Path) -> set[str]:
    """The folders and modules directly under ``adapters/``: each is one adapter area."""
    return {
        entry.stem if entry.is_file() else entry.name
        for entry in adapters.iterdir()
        if (entry.is_dir() and not entry.name.startswith(("__", ".")))
        or (entry.suffix == ".py" and entry.name != "__init__.py")
    }


def listed_as_independent() -> set[str]:
    pyproject = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    (contract,) = (
        c for c in pyproject["tool"]["importlinter"]["contracts"] if c["type"] == "independence"
    )
    prefix = "research_api.adapters."
    assert all(module.startswith(prefix) for module in contract["modules"])
    return {module.removeprefix(prefix) for module in contract["modules"]}


def test_every_adapter_area_is_listed_as_independent() -> None:
    """The independence contract is an explicit list, so a new area must be added to it."""
    present = adapter_areas(PACKAGE / SRC / "adapters")
    listed = listed_as_independent()
    assert present - listed == set(), "add to the independence contract in pyproject.toml"
    assert listed - present == set(), "listed in pyproject.toml but not in adapters/"


def test_a_new_adapter_area_is_noticed(tmp_path: Path) -> None:
    (tmp_path / "__init__.py").touch()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "persistence").mkdir()
    (tmp_path / "auth").mkdir()
    (tmp_path / "sessions.py").touch()
    assert adapter_areas(tmp_path) == {"persistence", "auth", "sessions"}
