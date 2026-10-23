"""Asking over HTTP, against the fake engine and a scripted one."""

from __future__ import annotations

import json
import logging
import threading
import time

import pytest
from ask_helpers import ANA, BETO, TEMP, ScriptedEngine, ask, delete, follow, post, running_app
from ask_helpers import sign_in as sign_in_as

from research_api.adapters.fake import FakeAskEngine
from research_api.adapters.fake.corpus import GROVER, TRANSMON
from research_api.application.ask import PER_USER_OPEN

ASKS = "/api/v1/asks"
PROBLEM = "application/problem+json"


def test_the_whole_path_from_question_to_answer() -> None:
    with running_app() as (client, _):
        done = post(client, ASKS, json={"question": "What limits T1 in transmons?"})
        assert done.status_code == 202
        assert done.json()["position"] == 0
        ask_id = done.json()["ask_id"]
        assert "no-store" in done.headers["cache-control"]

        events = follow(client, ask_id)
        assert [(e["event"], e["id"]) for e in events] == [
            ("queued", "0"),
            ("retrieving", "1"),
            ("answering", "2"),
            ("answer", "3"),
        ]
        assert events[0]["data"] == {"ask_id": ask_id, "position": 0}
        answer = events[-1]["data"]
        assert answer["ask_id"] == ask_id
        assert "dielectric loss" in answer["answer"]
        assert "References" not in answer["answer"]
        assert answer["references_text"].startswith(f"1. ({TRANSMON.short_id} pages 2-2)")
        assert {c["doc_id"] for c in answer["citations"]} == {TRANSMON.doc_id}
        assert [(c["first_page"], c["last_page"]) for c in answer["citations"]] == [
            (2, 2),
            (1, 2),
            (3, 3),
        ]
        assert answer["sources"] == [TRANSMON.doc_id]
        assert answer["contexts"] and answer["contexts"][0]["doc_id"] == TRANSMON.doc_id
        assert answer["contexts"][0]["score"] is None  # the field exists; docingest gives none
        assert set(answer["timings"]) == {"retrieve_s", "answer_s", "wall_s"}
        assert answer["index_generation"] == "fake-0001"

        view = client.get(f"{ASKS}/{ask_id}")
        assert view.status_code == 200 and "no-store" in view.headers["cache-control"]
        body = view.json()
        assert body["state"] == "done" and body["response"] == answer
        assert (body["position"] is None and body["phase"] is None and "error" not in body) or (
            body["error"] is None
        )


def test_a_stream_ends_without_the_answer_when_the_session_ended_meanwhile() -> None:
    engine = ScriptedEngine()
    gate = engine.hold("held")
    with running_app(engine) as (client, _):
        ask_id = ask(client, "held")
        seen: list = []
        reader = threading.Thread(target=lambda: seen.extend(follow(client, ask_id)))
        reader.start()
        time.sleep(0.2)  # the reader is connected and has seen the phases
        assert post(client, "/api/v1/auth/logout").status_code == 204
        gate.set()
        reader.join(5)
        assert not reader.is_alive()
        assert [e["event"] for e in seen] == ["queued", "retrieving", "answering"]
        assert "Answer to" not in json.dumps(seen)


def test_the_event_stream_is_not_cached() -> None:
    with running_app() as (client, _):
        ask_id = ask(client, "grover")
        stream = client.get(f"{ASKS}/{ask_id}/events")
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert stream.headers["cache-control"] == "no-store"


def test_a_spanish_question_gets_a_spanish_answer_with_the_same_papers() -> None:
    with running_app() as (client, _):
        answer = follow(client, ask(client, "¿Qué limita la coherencia de los transmones?"))[-1]
        assert answer["event"] == "answer"
        assert "pérdidas dieléctricas" in answer["data"]["answer"]
        assert answer["data"]["sources"] == [TRANSMON.doc_id]


def test_a_question_the_corpus_cannot_answer_says_so_without_sources() -> None:
    with running_app() as (client, _):
        answer = follow(client, ask(client, "Who won the 1998 football world cup?"))[-1]["data"]
        assert "do not contain enough information" in answer["answer"]
        assert answer["citations"] == [] and answer["contexts"] == [] and answer["sources"] == []
        assert answer["references_text"] == ""


def test_a_warning_of_the_engine_reaches_the_answer() -> None:
    with running_app() as (client, _):
        answer = follow(client, ask(client, "surface code threshold"))[-1]["data"]
        assert answer["warnings"] and "docingest index build" in answer["warnings"][0]


def test_without_the_index_there_is_no_retrieval_phase_and_no_contexts() -> None:
    with running_app() as (client, _):
        events = follow(client, ask(client, "grover search", use_index=False))
        assert [e["event"] for e in events] == ["queued", "answering", "answer"]
        assert events[-1]["data"]["contexts"] == []
        assert events[-1]["data"]["citations"]  # the answer still cites


def test_the_contexts_can_be_left_out() -> None:
    with running_app() as (client, _):
        answer = follow(client, ask(client, "grover search", return_contexts=False))[-1]["data"]
        assert answer["contexts"] == [] and answer["citations"]
        assert answer["sources"] == [GROVER.doc_id]


def test_the_options_reach_the_engine() -> None:
    engine = ScriptedEngine()
    with running_app(engine) as (client, _):
        follow(client, ask(client, "  padded  ", bm25="never", whole_blocks=True))
        sent = engine.options[0]
        assert sent.text == "padded"  # the question is trimmed
        assert (sent.options.bm25, sent.options.whole_blocks, sent.options.use_index) == (
            "never",
            True,
            True,
        )


@pytest.mark.parametrize(
    ("body", "pointer"),
    [
        ({}, "/question"),
        ({"question": ""}, "/question"),
        ({"question": "   "}, "/question"),
        ({"question": "x" * 4001}, "/question"),
        ({"question": "ok", "bm25": "sometimes"}, "/bm25"),
        ({"question": "ok", "use_index": "yes"}, "/use_index"),
        ({"question": "ok", "whole_blocks": 3}, "/whole_blocks"),
        ({"question": "ok", "colour": "red"}, "/colour"),
    ],
)
def test_a_bad_question_is_a_422_that_names_the_field_and_never_echoes_it(
    body: dict, pointer: str
) -> None:
    with running_app() as (client, _):
        done = post(client, ASKS, json=body)
        assert done.status_code == 422
        assert done.headers["content-type"] == PROBLEM
        assert pointer in [e["path"] for e in done.json()["errors"]]
        if "question" in body and len(str(body["question"])) > 100:
            assert "xxxxxxxxxx" not in done.text


def test_malformed_json_is_a_400() -> None:
    with running_app() as (client, _):
        done = post(
            client, ASKS, content=b"{not json", headers={"content-type": "application/json"}
        )
        assert done.status_code == 400


def test_everything_needs_a_signed_in_member() -> None:
    with running_app() as (client, _):
        ask_id = ask(client, "grover")
        client.cookies.clear()
        for method, path in (
            ("POST", ASKS),
            ("GET", f"{ASKS}/{ask_id}"),
            ("GET", f"{ASKS}/{ask_id}/events"),
            ("DELETE", f"{ASKS}/{ask_id}"),
            ("GET", "/api/v1/documents"),
            ("GET", f"/api/v1/documents/{TRANSMON.doc_id}"),
            ("GET", f"/api/v1/documents/{TRANSMON.doc_id}/pages/1"),
        ):
            done = client.request(
                method, path, json={"question": "x"} if method == "POST" else None
            )
            assert done.status_code == 401, (method, path)
            assert done.headers["content-type"] == PROBLEM


def test_an_account_that_must_change_its_password_cannot_ask() -> None:
    with running_app() as (client, _):
        sign_in_as(client, TEMP)
        assert post(client, ASKS, json={"question": "x"}).status_code == 403
        assert client.get("/api/v1/documents").status_code == 403


def test_a_write_without_the_csrf_header_is_refused() -> None:
    with running_app() as (client, _):
        assert client.post(ASKS, json={"question": "grover"}).status_code == 403
        ask_id = ask(client, "grover")
        assert client.delete(f"{ASKS}/{ask_id}").status_code == 403
        assert client.get(f"{ASKS}/{ask_id}").status_code == 200  # reads need no header


def test_a_page_on_another_origin_cannot_ask() -> None:
    with running_app() as (client, _):
        done = post(
            client, ASKS, json={"question": "grover"}, headers={"Origin": "http://evil.example"}
        )
        assert done.status_code == 403


def test_one_member_cannot_see_or_cancel_another_members_question() -> None:
    with running_app() as (client, _):
        ask_id = ask(client, "grover")
        follow(client, ask_id)
        sign_in_as(client, BETO)
        for call in (
            lambda: client.get(f"{ASKS}/{ask_id}"),
            lambda: client.get(f"{ASKS}/{ask_id}/events"),
            lambda: delete(client, f"{ASKS}/{ask_id}"),
        ):
            done = call()
            assert done.status_code == 404
            assert "grover" not in done.text
        sign_in_as(client, ANA)
        assert client.get(f"{ASKS}/{ask_id}").status_code == 200  # still there for its owner


def test_an_admin_does_not_read_other_members_questions_either() -> None:
    with running_app() as (client, _):
        sign_in_as(client, BETO)
        ask_id = ask(client, "grover")
        follow(client, ask_id)
        sign_in_as(client, ANA)  # an admin
        assert client.get(f"{ASKS}/{ask_id}").status_code == 404


def test_an_unknown_question_is_a_404_problem() -> None:
    with running_app() as (client, _):
        done = client.get(f"{ASKS}/nope")
        assert done.status_code == 404 and done.headers["content-type"] == PROBLEM
        assert "no-store" in done.headers["cache-control"]


def test_a_finished_question_can_be_forgotten() -> None:
    with running_app() as (client, _):
        ask_id = ask(client, "grover")
        follow(client, ask_id)
        assert delete(client, f"{ASKS}/{ask_id}").status_code == 204
        assert client.get(f"{ASKS}/{ask_id}").status_code == 404


# --- reconnecting -----------------------------------------------------------------------------


def test_a_reconnecting_browser_gets_only_what_it_missed() -> None:
    with running_app() as (client, _):
        ask_id = ask(client, "grover")
        full = follow(client, ask_id)
        resumed = follow(client, ask_id, **{"Last-Event-ID": "1"})
        assert [e["id"] for e in resumed] == ["2", "3"]
        assert resumed == full[2:]


def test_a_browser_that_has_seen_the_last_event_is_told_to_stop_reconnecting() -> None:
    with running_app() as (client, _):
        ask_id = ask(client, "grover")
        follow(client, ask_id)
        done = client.get(f"{ASKS}/{ask_id}/events", headers={"Last-Event-ID": "3"})
        assert done.status_code == 204 and done.content == b""
        beyond = client.get(f"{ASKS}/{ask_id}/events", headers={"Last-Event-ID": "40"})
        assert beyond.status_code == 204


def test_a_bad_last_event_id_is_a_422() -> None:
    with running_app() as (client, _):
        ask_id = ask(client, "grover")
        done = client.get(f"{ASKS}/{ask_id}/events", headers={"Last-Event-ID": "abc"})
        assert done.status_code == 422


# --- failures ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "status", "kind", "retry"),
    [
        ("simulate-unavailable", 503, "unavailable", 5),
        ("simulate-upstream", 502, "upstream-error", None),
    ],
)
def test_a_failure_arrives_as_an_error_event_and_in_the_view(
    word: str, status: int, kind: str, retry: int | None
) -> None:
    with running_app() as (client, _):
        ask_id = ask(client, f"grover {word}")
        events = follow(client, ask_id)
        assert [e["event"] for e in events][-1] == "error"
        error = events[-1]["data"]
        assert (error["status"], error["kind"], error.get("retry_after_s")) == (status, kind, retry)
        assert error["message"]
        view = client.get(f"{ASKS}/{ask_id}").json()
        assert view["state"] == "failed" and view["error"] == error and view["response"] is None
        # the lane is free: the next question is answered
        assert follow(client, ask(client, "grover"))[-1]["event"] == "answer"


def test_a_crash_is_an_error_with_an_id_and_the_question_stays_out_of_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    question = "grover simulate-crash my confidential wording"
    with caplog.at_level(logging.INFO, logger="research_api"), running_app() as (client, _):
        error = follow(client, ask(client, question))[-1]["data"]
        assert error["status"] == 500 and error["kind"] == "internal"
        assert "Error id" in error["message"]
        assert "confidential" not in error["message"]
    assert "confidential" not in caplog.text and "simulated crash" not in caplog.text
    assert any(getattr(r, "event", "") == "ask.error" for r in caplog.records)


def test_asks_are_refused_while_the_engine_starts_and_the_status_says_so() -> None:
    engine = ScriptedEngine("warming_up")

    async def stays_cold() -> None:
        return None

    engine.warm_up = stays_cold  # type: ignore[method-assign]
    with running_app(engine) as (client, _):
        done = post(client, ASKS, json={"question": "x"})
        assert done.status_code == 503 and done.headers["content-type"] == PROBLEM
        assert int(done.headers["retry-after"]) > 0
        assert client.get("/api/v1/status").json()["ask"]["state"] == "warming_up"


def test_too_many_open_questions_of_one_member_is_a_429() -> None:
    engine = ScriptedEngine()
    gate = engine.hold("held")
    with running_app(engine) as (client, _):
        for _ in range(PER_USER_OPEN):
            ask(client, "held")
        done = post(client, ASKS, json={"question": "held"})
        assert done.status_code == 429 and int(done.headers["retry-after"]) > 0
        sign_in_as(client, BETO)
        ask(client, "held")  # another member is not held back
        gate.set()


# --- the queue --------------------------------------------------------------------------------


def test_questions_wait_in_line_and_the_status_shows_the_lane() -> None:
    engine = ScriptedEngine()
    gate = engine.hold("first")
    with running_app(engine) as (client, _):
        first = ask(client, "first")
        sign_in_as(client, BETO)
        second = ask(client, "second")
        view = client.get(f"{ASKS}/{second}").json()
        assert view["state"] == "queued" and view["position"] == 1
        lane = client.get("/api/v1/status").json()["ask"]
        assert (lane["queued"], lane["busy"], lane["state"]) == (1, True, "ready")

        results: list = []
        reader = threading.Thread(target=lambda: results.extend(follow(client, second)))
        reader.start()
        time.sleep(0.1)
        gate.set()
        reader.join(5)
        assert [(e["event"], e["data"].get("position")) for e in results][:3] == [
            ("queued", 1),
            ("queued", 0),
            ("retrieving", None),
        ]
        assert results[-1]["event"] == "answer"
        assert engine.asked == ["first", "second"]
        sign_in_as(client, ANA)
        assert client.get(f"{ASKS}/{first}").json()["state"] == "done"


def test_a_waiting_question_is_cancelled_by_its_owner() -> None:
    engine = ScriptedEngine()
    gate = engine.hold("first")
    with running_app(engine) as (client, _):
        ask(client, "first")
        second = ask(client, "second")
        assert delete(client, f"{ASKS}/{second}").status_code == 204
        events = follow(client, second)
        assert [e["event"] for e in events] == ["queued", "cancelled"]
        assert client.get(f"{ASKS}/{second}").json()["state"] == "cancelled"
        gate.set()
        time.sleep(0.1)
        assert engine.asked == ["first"]  # the engine never saw it


def test_the_status_block_of_the_engine() -> None:
    with running_app() as (client, _):
        status = client.get("/api/v1/status").json()
        assert status["ask"] == {
            "state": "ready",
            "reason": None,
            "index_generation": "fake-0001",
            "queued": 0,
            "busy": False,
        }
        client.cookies.clear()
        assert client.get("/api/v1/status").status_code == 401  # who is asking is for members


def test_the_fake_engine_pauses_between_phases_when_asked_to() -> None:
    with running_app(FakeAskEngine(pause_s=0.05)) as (client, _):
        started = time.perf_counter()
        events = follow(client, ask(client, "grover"))
        assert time.perf_counter() - started >= 0.14  # 0.05 retrieving + 0.1 answering
        assert events[-1]["data"]["timings"]["wall_s"] >= 0.14


def test_the_logs_name_the_route_and_never_the_question_or_the_ask_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "grover search with my confidential wording"
    with caplog.at_level(logging.INFO, logger="research_api"), running_app() as (client, _):
        ask_id = ask(client, secret)
        follow(client, ask_id)
        client.get(f"{ASKS}/{ask_id}")
        delete(client, f"{ASKS}/{ask_id}")
        client.get("/api/v1/documents", params={"query": "confidential-title-query"})
    routes = {getattr(r, "route", None) for r in caplog.records if r.name == "research_api.access"}
    assert "/api/v1/asks/{ask_id}/events" in routes and "/api/v1/asks/{ask_id}" in routes
    assert "/api/v1/documents" in routes
    assert "confidential" not in caplog.text
    assert ask_id not in caplog.text
