"""Write tests/fixtures/bridge/*.json: the circuit/1 documents docingest makes from the LaTeX
sources next to them.

quantum_sim never imports docingest, so the bridge tests (tests/test_bridge.py) read these saved
documents instead. They are produced by docingest's own ``gate_list(...).circuits[0].to_dict()``
and checked against it on every docingest run by
packages/docingest/tests/unit/test_quantum_sim_fixtures.py, so a change in docingest's output
fails there until this script is run again. Run it from a checkout where docingest is installed:

    uv run --package docingest python packages/quantum-sim/tools/make_bridge_fixtures.py

Add a circuit by adding NAME.tex (the body of one circuit code block) and running the script.
A source must read as one ``parsed`` circuit, except one named ``partial_*.tex``, which must read
as one ``partial`` circuit (a drawing with a left-out part that quantum-sim has to refuse).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from docingest.domain.circuits import gate_list

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "bridge"


def main() -> int:
    for source in sorted(FIXTURES.glob("*.tex")):
        found = gate_list(source.read_text(encoding="utf-8"))
        expected = "partial" if source.stem.startswith("partial_") else "parsed"
        if found.status != expected or len(found.circuits) != 1:
            got = found.status
            sys.stderr.write(f"{source.name}: expected one {expected} circuit, got {got}\n")
            return 1
        target = source.with_suffix(".json")
        document = found.circuits[0].to_dict()
        target.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        sys.stdout.write(f"{target.name}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
