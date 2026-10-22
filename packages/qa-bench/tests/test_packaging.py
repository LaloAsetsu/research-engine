"""What the package promises about itself: one dependency, no heavy imports, schemas shipped."""

import subprocess
import sys
from importlib.metadata import requires
from pathlib import Path

from qa_bench.cli import SHOWN
from qa_bench.convert import SHAPES
from qa_bench.formats import FORMATS
from qa_bench.formats.schema import schema_name

PACKAGE = Path(__file__).parents[1]


def test_the_only_runtime_dependency_is_jsonschema():
    assert requires("qa-bench") == ["jsonschema>=4.25"]


def test_importing_the_package_loads_none_of_the_forbidden_libraries():
    code = (
        "import sys, qa_bench.cli; "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'httpx', 'numpy', 'docingest', 'docingest_index', 'quantum_sim', 'research_api'}))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_every_schema_is_in_the_source_tree_where_the_package_reads_it():
    folder = PACKAGE / "src" / "qa_bench" / "formats" / "schemas"
    assert sorted(p.name for p in folder.glob("*.json")) == sorted(schema_name(f) for f in FORMATS)


def test_the_readme_documents_every_format_shape_and_command():
    text = (PACKAGE / "README.md").read_text(encoding="utf-8")
    for fmt in FORMATS:
        assert f"`{fmt}`" in text, fmt
    for shape in SHAPES:
        assert f"`{shape}`" in text, shape
    for command in ("qa-bench validate", "qa-bench convert", "ask-capture", "--force"):
        assert command in text, command
    assert str(SHOWN) in text
