from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from research_api.entrypoints import cli


def test_openapi_prints_sorted_stable_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["openapi"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["openapi"]) == 0
    assert capsys.readouterr().out == first
    document = json.loads(first)
    assert document["info"]["title"] == "Research engine API"
    assert first == json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert "/api/v1/health" in document["paths"]


def test_openapi_can_write_a_file(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"
    assert cli.main(["openapi", "-o", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == cli.openapi_document()


def openapi_in_new_process(hash_seed: str) -> str:
    done = subprocess.run(
        [sys.executable, "-m", "research_api.entrypoints.cli", "openapi"],
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


def test_openapi_is_the_same_text_in_every_process() -> None:
    """Set order differs between processes under other hash seeds; the committed copy must not."""
    first, second = openapi_in_new_process("1"), openapi_in_new_process("4242")
    assert first == second == cli.openapi_document()


def test_openapi_lists_the_modes_of_the_status() -> None:
    schema = json.loads(cli.openapi_document())["components"]["schemas"]["Status"]
    assert schema["properties"]["mode"]["enum"] == ["live", "fake"]


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_run(app: object, **kwargs: Any) -> None:
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    monkeypatch.delenv("RESEARCH_API_CONFIG", raising=False)
    return calls


def test_serve_fake_listens_on_loopback_without_an_access_log(served: list[dict[str, Any]]) -> None:
    assert cli.main(["serve", "--fake"]) == 0
    (call,) = served
    assert (call["host"], call["port"], call["access_log"]) == ("127.0.0.1", 8090, False)


def test_serve_reads_the_settings_file(served: list[dict[str, Any]], tmp_path: Path) -> None:
    path = tmp_path / "s.toml"
    path.write_text("port = 9001\n", encoding="utf-8")
    assert cli.main(["serve", "--config", str(path)]) == 0
    assert served[0]["port"] == 9001


def test_serve_reads_the_file_named_in_the_environment(
    served: list[dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "s.toml"
    path.write_text("port = 9002\n", encoding="utf-8")
    monkeypatch.setenv("RESEARCH_API_CONFIG", str(path))
    assert cli.main(["serve"]) == 0
    assert served[0]["port"] == 9002


def test_serve_without_settings_says_what_to_do(
    served: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["serve"]) == 1
    assert "--config" in capsys.readouterr().err
    assert served == []


def test_serve_with_a_refused_setting_exits_1(
    served: list[dict[str, Any]], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "s.toml"
    path.write_text('bind_host = "0.0.0.0"\n', encoding="utf-8")
    assert cli.main(["serve", "--config", str(path)]) == 1
    assert "bind_host" in capsys.readouterr().err
    assert served == []


def test_config_and_fake_cannot_be_combined() -> None:
    with pytest.raises(SystemExit) as stop:
        cli.main(["serve", "--fake", "--config", "x.toml"])
    assert stop.value.code == 2


def test_serve_fake_takes_a_port(served: list[dict[str, Any]]) -> None:
    assert cli.main(["serve", "--fake", "--port", "18090"]) == 0
    assert (served[0]["host"], served[0]["port"]) == ("127.0.0.1", 18090)


def test_a_port_on_the_command_line_beats_the_settings_file(
    served: list[dict[str, Any]], tmp_path: Path
) -> None:
    path = tmp_path / "s.toml"
    path.write_text("port = 9001\n", encoding="utf-8")
    assert cli.main(["serve", "--config", str(path), "--port", "9005"]) == 0
    assert served[0]["port"] == 9005


def test_a_port_out_of_range_exits_1(
    served: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["serve", "--fake", "--port", "70000"]) == 1
    assert "--port" in capsys.readouterr().err
    assert served == []
