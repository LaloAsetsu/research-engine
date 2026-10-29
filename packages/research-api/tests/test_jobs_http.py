"""The benchmark routes over HTTP: who may call them, what they answer, and the whole path."""

from __future__ import annotations

import json
from typing import Any

import pytest
from ask_helpers import ANA, BETO, TEMP, ScriptedEngine, delete, post, sign_in
from bench_helpers import (
    DEMO,
    DEMO_IDS,
    JOBS,
    RUNS,
    SCORES,
    ScriptedArms,
    add_quotes_set,
    bench_app,
    in_state,
    run_body,
    settled,
    start_run,
    wait_for,
)

from research_api.adapters.fake import FakeModelServer
from research_api.application.arms import LiveArms
from research_api.domain.jobs import MAX_OPEN_PER_MEMBER

PROBLEM = "application/problem+json"
ALL = "/api/v1"


# --- who may call -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/models"),
        ("post", "/runs"),
        ("get", "/runs"),
        ("get", "/runs/x"),
        ("get", "/runs/x/items"),
        ("post", "/scores"),
        ("get", "/scores"),
        ("get", f"/scores/{'0' * 26}"),
        ("get", f"/scores/{'0' * 26}/items"),
        ("get", "/jobs"),
        ("get", f"/jobs/{'0' * 26}"),
        ("get", f"/jobs/{'0' * 26}/log"),
        ("post", f"/jobs/{'0' * 26}/cancel"),
        ("post", f"/jobs/{'0' * 26}/resume"),
    ],
)
def test_every_route_needs_a_session(method: str, path: str) -> None:
    with bench_app(signed_in=None) as (client, _):
        done = getattr(client, method)(ALL + path)
        assert done.status_code == 401
        assert done.headers["content-type"].startswith(PROBLEM)


@pytest.mark.parametrize(
    "path", ["/runs", "/scores", f"/jobs/{'0' * 26}/cancel", f"/jobs/{'0' * 26}/resume"]
)
def test_a_write_needs_the_csrf_header(path: str) -> None:
    with bench_app() as (client, _):
        done = client.post(ALL + path, json={})
        assert done.status_code == 403 and "CSRF" in done.json()["detail"]


def test_a_member_who_must_change_the_password_may_not_use_them() -> None:
    with bench_app(signed_in=TEMP) as (client, _):
        assert client.get(f"{ALL}/models").status_code == 403
        assert client.get(f"{ALL}/runs").status_code == 403


def test_nothing_is_cached_by_the_browser() -> None:
    with bench_app() as (client, _):
        job = start_run(client)
        settled(client, job["id"])
        for path in (RUNS, f"{RUNS}/{job['run']['run_id']}", JOBS, f"{JOBS}/{job['id']}"):
            done = client.get(path)
            assert done.status_code == 200 and done.headers["cache-control"] == "no-store", path
        assert client.get(f"{JOBS}/{job['id']}/log").headers["cache-control"] == "no-store"


# --- models -----------------------------------------------------------------------------------


def test_the_models_say_which_can_be_chosen() -> None:
    with bench_app() as (client, _):
        found = client.get(f"{ALL}/models")
        assert found.status_code == 200
        assert [(m["name"], m["available"]) for m in found.json()] == [
            ("fake-model", True),
            ("fake-offline", False),
        ]
        assert set(found.json()[0]) == {"name", "label", "revision", "available"}


# --- starting a run ---------------------------------------------------------------------------


def test_a_run_is_started_followed_and_ends_with_its_file() -> None:
    with bench_app(ScriptedArms()) as (client, _):
        done = post(client, RUNS, json=run_body("bare", repeats=2, notes="hi"))
        assert done.status_code == 202
        (job,) = done.json()
        assert job["kind"] == "run" and job["state"] in ("queued", "running", "done")
        assert job["settled"] is False or job["state"] == "done"
        assert job["owner"] == "ana" and job["set"] == DEMO
        assert job["run"]["arm"] == {"name": "bare", "variant": None, "overrides": {}}
        assert job["run"]["model"] == "fake-model" and job["run"]["notes"] == "hi"
        assert job["progress"]["total"] == 2 * len(DEMO_IDS) and job["score"] is None
        end = settled(client, job["id"])
        assert end["state"] == "done" and end["progress"] == {"done": 12, "total": 12}
        assert end["started_at"] and end["ended_at"] and end["error"] is None
        assert end["position"] is None and end["waiting_for"] is None
        run = client.get(f"{RUNS}/{job['run']['run_id']}").json()
        assert (run["answers"], run["errors"], run["complete"], run["state"]) == (
            12,
            0,
            True,
            "done",
        )
        assert run["job_id"] == job["id"] and run["owner"] == "ana" and run["expected"] == 12
        assert run["set_name"] == "demo-papers/v1" and run["arm"] == "bare"


def test_three_arms_make_three_jobs_that_run_one_after_the_other() -> None:
    arms = ScriptedArms()
    gate = arms.hold("DP-001-es")
    body = {
        "set": DEMO,
        "arms": [
            {"name": "bare"},
            {"name": "harness", "overrides": {"bm25": "never"}},
            {"name": "oracle"},
        ],
        "models": ["fake-model"],
    }
    with bench_app(arms) as (client, _):
        done = post(client, RUNS, json=body)
        assert done.status_code == 202
        first, second, third = done.json()
        assert [j["run"]["arm"]["name"] for j in (first, second, third)] == [
            "bare",
            "harness",
            "oracle",
        ]
        assert second["run"]["arm"]["overrides"] == {"bm25": "never"}
        wait_for(client, first["id"], lambda j: j["progress"]["done"] == 1)
        queue = [client.get(f"{JOBS}/{j['id']}").json() for j in (first, second, third)]
        assert [(j["state"], j["position"]) for j in queue] == [
            ("running", None),
            ("queued", 1),
            ("queued", 2),
        ]
        status = client.get(f"{ALL}/status").json()["bench"]
        assert status == {
            "running_job": first["id"],
            "queued_runs": 2,
            "scoring": False,
            "queued_scores": 0,
        }
        gate.set()
        for j in (first, second, third):
            assert settled(client, j["id"])["state"] == "done"
        assert arms.max_active == 1
        assert client.get(f"{ALL}/status").json()["bench"]["running_job"] is None


@pytest.mark.parametrize(
    ("body", "status", "path"),
    [
        ({"arms": [{"name": "bare"}], "models": ["fake-model"]}, 422, "/set"),
        (run_body() | {"extra": 1}, 422, "/extra"),
        (run_body("agent"), 422, "/arms/0/name"),
        (run_body(repeats=0), 422, "/repeats"),
        (run_body(repeats=11), 422, "/repeats"),
        (run_body(model="nobody"), 422, "/models/0"),
        (run_body() | {"models": []}, 422, "/models"),
        (run_body() | {"arms": []}, 422, "/arms"),
        (run_body() | {"set": {"name": "x/y", "version": "v1"}}, 422, "/set/name"),
        (run_body() | {"set": {"name": "demo-papers", "version": "1"}}, 422, "/set/version"),
        (
            run_body() | {"arms": [{"name": "bare", "overrides": {"use_index": False}}]},
            422,
            "/arms/0/overrides",
        ),
        (
            run_body() | {"arms": [{"name": "harness", "overrides": {"bm25": "x"}}]},
            422,
            "/arms/0/overrides/bm25",
        ),
        (run_body() | {"notes": "n" * 1001}, 422, "/notes"),
        (run_body() | {"set": {"name": "demo-papers", "version": "v9"}}, 404, None),
        (run_body() | {"set": {"name": "demo-papers", "version": "v2"}}, 409, None),
        (run_body(model="fake-offline"), 503, None),
        (run_body("harness", model="fake-offline"), 422, "/arms/0"),
        (run_body("oracle", model="fake-offline"), 422, "/arms/0"),
    ],
)
def test_a_run_that_cannot_be_started_is_told_why(
    body: dict[str, Any], status: int, path: str | None
) -> None:
    with bench_app(ScriptedArms()) as (client, _):
        done = post(client, RUNS, json=body)
        assert done.status_code == status, done.text
        assert done.headers["content-type"].startswith(PROBLEM)
        if path is not None:
            assert path in [e["path"] for e in done.json()["errors"]], done.json()
        assert client.get(JOBS).json() == []  # nothing was made


def test_a_model_that_is_not_docingests_is_refused_for_the_engine_arms_with_the_right_one() -> None:
    with bench_app(ScriptedArms()) as (client, _):
        done = post(client, RUNS, json=run_body("harness", model="fake-offline"))
        (error,) = done.json()["errors"]
        assert error["path"] == "/arms/0"
        assert "answers with the model docingest is set up with, fake-model" in error["message"]
        assert done.json()["detail"] == error["message"]  # a client may show only the detail
        assert post(client, RUNS, json=run_body("harness")).status_code == 202


def test_the_oracle_on_a_set_with_a_gold_passage_without_text_is_a_422_that_names_the_questions() -> (
    None
):
    with bench_app(ScriptedArms()) as (client, container):
        body = run_body("bare") | {"arms": [{"name": "bare"}, {"name": "oracle"}]}
        body["set"] = {"name": add_quotes_set(container).name, "version": "v1"}
        done = post(client, RUNS, json=body)
        assert done.status_code == 422 and done.headers["content-type"].startswith(PROBLEM)
        (error,) = done.json()["errors"]
        assert error["path"] == "/arms/1"
        assert "7 of 9 questions of quotes-only/v1" in error["message"]
        assert done.json()["detail"] == error["message"]  # a client may show only the detail
        assert client.get(JOBS).json() == []  # not even the closed-book job was made


def test_the_oracle_runs_on_the_demo_set_whose_unanswerable_question_has_no_gold_paper() -> None:
    engine = ScriptedEngine()
    arms = LiveArms(engine=engine, models=FakeModelServer())
    with bench_app(arms, ask_engine=engine) as (client, _):
        done = post(client, RUNS, json=run_body("oracle"))
        assert done.status_code == 202, done.text
        assert settled(client, done.json()[0]["id"])["state"] == "done"


def test_a_draft_is_refused_with_the_way_out() -> None:
    with bench_app(ScriptedArms()) as (client, _):
        body = run_body() | {"set": {"name": "demo-papers", "version": "v2"}}
        done = post(client, RUNS, json=body)
        assert done.status_code == 409 and "freeze it first" in done.json()["detail"]


def test_a_key_the_api_does_not_know_is_an_error() -> None:
    with bench_app(ScriptedArms()) as (client, _):
        done = post(client, RUNS, json=run_body() | {"model": "fake-model"})
        assert done.status_code == 422


def test_too_many_waiting_jobs_are_a_429_with_retry_after() -> None:
    arms = ScriptedArms()
    arms.hold("DP-001")
    with bench_app(arms) as (client, _):
        for index in range(MAX_OPEN_PER_MEMBER):
            done = post(
                client, RUNS, json=run_body() | {"arms": [{"name": "bare", "variant": f"v{index}"}]}
            )
            assert done.status_code == 202
        done = post(client, RUNS, json=run_body())
        assert done.status_code == 429 and int(done.headers["retry-after"]) > 0
        sign_in(client, BETO)
        assert post(client, RUNS, json=run_body()).status_code == 202


def test_an_unavailable_arm_is_a_503() -> None:
    arms = ScriptedArms()
    arms.refused["oracle"] = "No oracle in this build."
    with bench_app(arms) as (client, _):
        done = post(client, RUNS, json=run_body("oracle"))
        assert done.status_code == 503 and done.json()["detail"] == "No oracle in this build."


# --- the jobs list ----------------------------------------------------------------------------


def test_jobs_are_listed_newest_first_and_can_be_filtered() -> None:
    with bench_app(ScriptedArms()) as (client, _):
        first = start_run(client, "bare")
        settled(client, first["id"])
        sign_in(client, BETO)
        second = start_run(client, "harness")
        settled(client, second["id"])
        listed = client.get(JOBS).json()
        assert [j["id"] for j in listed] == [second["id"], first["id"]]
        assert [j["id"] for j in client.get(JOBS, params={"mine": "true"}).json()] == [second["id"]]
        assert [j["id"] for j in client.get(JOBS, params={"kind": "score"}).json()] == []
        assert len(client.get(JOBS, params={"state": "done"}).json()) == 2
        assert client.get(JOBS, params={"state": "queued"}).json() == []
        assert len(client.get(JOBS, params={"limit": 1}).json()) == 1
        assert [j["id"] for j in client.get(JOBS, params={"limit": 1, "offset": 1}).json()] == [
            first["id"]
        ]
        for bad in (
            {"state": "paused"},
            {"kind": "walk"},
            {"limit": 0},
            {"limit": 201},
            {"offset": -1},
        ):
            assert client.get(JOBS, params=bad).status_code == 422


def test_one_job_by_id_and_what_is_not_an_id() -> None:
    with bench_app(ScriptedArms()) as (client, _):
        job = start_run(client)
        assert client.get(f"{JOBS}/{job['id']}").json()["id"] == job["id"]
        assert client.get(f"{JOBS}/{'0' * 26}").status_code == 404
        for bad in ("short", "I" * 26, "x" * 27, "..%2f..%2fetc"):
            assert client.get(f"{JOBS}/{bad}").status_code in (404, 422)
        settled(client, job["id"])


# --- the log ----------------------------------------------------------------------------------


def test_the_log_is_text_read_from_an_offset_with_where_to_go_on() -> None:
    with bench_app(ScriptedArms()) as (client, _):
        job = start_run(client)
        settled(client, job["id"])
        whole = client.get(f"{JOBS}/{job['id']}/log")
        assert whole.status_code == 200 and whole.headers["content-type"].startswith("text/plain")
        assert (
            int(whole.headers["x-log-next-offset"])
            == int(whole.headers["x-log-size"])
            == len(whole.content)
        )
        assert "queued by ana" in whole.text and "done: 6 answers" in whole.text
        first_line = whole.text.split("\n", 1)[0]
        tail = client.get(f"{JOBS}/{job['id']}/log", params={"offset": len(first_line) + 1})
        assert tail.text == whole.text[len(first_line) + 1 :]
        end = client.get(f"{JOBS}/{job['id']}/log", params={"offset": whole.headers["x-log-size"]})
        assert end.text == "" and end.headers["x-log-next-offset"] == whole.headers["x-log-size"]
        assert client.get(f"{JOBS}/{job['id']}/log", params={"offset": -1}).status_code == 422
        assert client.get(f"{JOBS}/{'0' * 26}/log").status_code == 404


def test_a_live_tail_sees_the_new_lines_only() -> None:
    arms = ScriptedArms()
    gate = arms.hold("DP-002")
    with bench_app(arms) as (client, _):
        job = start_run(client)
        wait_for(client, job["id"], lambda j: j["progress"]["done"] == 2)
        part = client.get(f"{JOBS}/{job['id']}/log")
        gate.set()
        settled(client, job["id"])
        more = client.get(
            f"{JOBS}/{job['id']}/log", params={"offset": part.headers["x-log-next-offset"]}
        )
        assert "3/6 DP-002" in more.text and "1/6 DP-001" not in more.text


# --- cancel and resume ------------------------------------------------------------------------


def test_cancel_then_resume_over_http_ends_with_every_question_once() -> None:
    arms = ScriptedArms()
    gate = arms.hold("DP-002")
    with bench_app(arms) as (client, _):
        job = start_run(client, repeats=2)
        wait_for(client, job["id"], lambda j: j["progress"]["done"] == 2)
        asked = post(client, f"{JOBS}/{job['id']}/cancel")
        assert asked.status_code == 200 and asked.json()["state"] == "cancelling"
        assert asked.json()["settled"] is False
        gate.set()
        stopped = in_state(client, job["id"], "cancelled")
        assert stopped["settled"] is True and stopped["progress"]["done"] == 3
        run_id = job["run"]["run_id"]
        summary = client.get(f"{RUNS}/{run_id}").json()
        assert (summary["answers"], summary["complete"], summary["ended"]) == (3, False, None)
        assert "Partial: Cancelled" in summary["notes"] and summary["state"] == "cancelled"
        resumed = post(client, f"{JOBS}/{job['id']}/resume")
        assert resumed.status_code == 200 and resumed.json()["state"] in ("queued", "running")
        done = in_state(client, job["id"], "done")
        assert done["progress"] == {"done": 12, "total": 12}
        items = client.get(f"{RUNS}/{run_id}/items", params={"limit": 200}).json()["items"]
        assert [(i["id"], i["repeat"]) for i in items] == [(i, r) for r in (1, 2) for i in DEMO_IDS]


def test_the_wrong_person_and_the_wrong_state() -> None:
    arms = ScriptedArms()
    gate = arms.hold("DP-001")
    with bench_app(arms) as (client, _):
        sign_in(client, BETO)
        job = start_run(client)
        sign_in(client, ANA)  # ana is an admin
        sign_in(client, {"username": "dani", "password": "test-password-dani-03"})
        denied = post(client, f"{JOBS}/{job['id']}/cancel")
        assert denied.status_code == 403 and denied.headers["content-type"].startswith(PROBLEM)
        assert post(client, f"{JOBS}/{job['id']}/resume").status_code == 403
        sign_in(client, ANA)
        assert post(client, f"{JOBS}/{job['id']}/resume").status_code == 409  # not stopped
        assert post(client, f"{JOBS}/{job['id']}/cancel").json()["state"] == "cancelling"
        gate.set()
        in_state(client, job["id"], "cancelled")
        assert post(client, f"{JOBS}/{job['id']}/cancel").status_code == 200  # twice is the same
        other = start_run(client, "harness")
        in_state(client, other["id"], "done")
        assert post(client, f"{JOBS}/{other['id']}/cancel").status_code == 409
        assert post(client, f"{JOBS}/{'0' * 26}/cancel").status_code == 404
        assert post(client, f"{JOBS}/short/cancel").status_code == 422


def test_a_queued_job_is_cancelled_over_http_and_leaves_no_file() -> None:
    arms = ScriptedArms()
    gate = arms.hold("DP-001")
    with bench_app(arms) as (client, _):
        first = start_run(client, "bare")
        second = start_run(client, "harness")
        wait_for(client, first["id"], lambda j: j["state"] == "running")
        cancelled = post(client, f"{JOBS}/{second['id']}/cancel")
        assert cancelled.json()["state"] == "cancelled" and cancelled.json()["settled"]
        gate.set()
        settled(client, first["id"])
        assert [r["run_id"] for r in client.get(RUNS).json()] == [first["run"]["run_id"]]
        assert client.get(f"{RUNS}/{second['run']['run_id']}").status_code == 404


# --- runs -------------------------------------------------------------------------------------


def test_the_items_of_a_run_come_with_their_questions() -> None:
    arms = ScriptedArms()
    from research_api.domain.errors import UpstreamError  # noqa: PLC0415

    arms.errors[("DP-002", 1)] = UpstreamError("The model server refused the request.")
    with bench_app(arms) as (client, _):
        job = start_run(client)
        settled(client, job["id"])
        run_id = job["run"]["run_id"]
        page = client.get(f"{RUNS}/{run_id}/items").json()
        assert (page["total"], page["offset"], page["limit"]) == (6, 0, 20)
        assert page["run"]["errors"] == 1 and page["run"]["answers"] == 6
        first = page["items"][0]
        assert first["id"] == "DP-001" and first["question"].startswith("What symbol")
        assert first["reference_answer"] == "J, the exchange coupling."
        assert first["answer"] == "bare answer to DP-001 #1" and first["error"] is None
        assert first["citations"] == [] and first["contexts"] == []
        assert first["timings"]["wall_s"] == 0.01
        failed = next(i for i in page["items"] if i["id"] == "DP-002")
        assert failed["answer"] is None and failed["error"]["kind"] == "other"
        errors = client.get(f"{RUNS}/{run_id}/items", params={"filter": "errors"}).json()
        assert [i["id"] for i in errors["items"]] == ["DP-002"] and errors["total"] == 1
        answered = client.get(f"{RUNS}/{run_id}/items", params={"filter": "answered"}).json()
        assert answered["total"] == 5 and "DP-002" not in [i["id"] for i in answered["items"]]
        one = client.get(f"{RUNS}/{run_id}/items", params={"id": "DP-004"}).json()
        assert [i["id"] for i in one["items"]] == ["DP-004"]
        paged = client.get(f"{RUNS}/{run_id}/items", params={"offset": 4, "limit": 5}).json()
        assert [i["id"] for i in paged["items"]] == ["DP-004", "DP-005"]
        for bad in ({"filter": "some"}, {"limit": 0}, {"limit": 201}, {"offset": -1}):
            assert client.get(f"{RUNS}/{run_id}/items", params=bad).status_code == 422
        assert client.get(f"{RUNS}/ghost/items").status_code == 404
        assert client.get(f"{RUNS}/.hidden").status_code == 422


def test_item_lines_of_the_wrong_type_in_a_hand_edited_run_do_not_break_the_page() -> None:
    wrong = [
        {"citations": 5},
        {"answer": 123},
        {"timings": "x"},
        {"contexts": [1, 2]},
        {"error": "x"},
    ]
    with bench_app() as (client, container):
        job = start_run(client)
        settled(client, job["id"])
        run_id = job["run"]["run_id"]
        extra = "".join(
            json.dumps({"id": "DP-001", "repeat": 9 + n, **fields}) + "\n"
            for n, fields in enumerate(wrong)
        )
        container.bench.jobs._files.append_run(run_id, extra.encode())
        page = client.get(f"{RUNS}/{run_id}/items", params={"limit": 200})
        assert page.status_code == 200 and page.json()["total"] == 6
        assert client.get(RUNS).status_code == 200


def test_contexts_can_be_left_out_of_a_page() -> None:
    with bench_app() as (client, _):  # the fake arms, with contexts
        job = start_run(client, "harness")
        settled(client, job["id"])
        run_id = job["run"]["run_id"]
        full = client.get(f"{RUNS}/{run_id}/items", params={"id": "DP-001"}).json()["items"][0]
        bare = client.get(
            f"{RUNS}/{run_id}/items", params={"id": "DP-001", "contexts": "false"}
        ).json()["items"][0]
        assert full["contexts"] and full["citations"] and bare["contexts"] == []
        assert full["contexts"][0]["doc_id"] == "a1" * 32 and "text" in full["contexts"][0]


def test_runs_are_listed_newest_first_from_the_files() -> None:
    with bench_app(ScriptedArms()) as (client, _):
        first = start_run(client, "bare")
        settled(client, first["id"])
        second = start_run(client, "harness")
        settled(client, second["id"])
        runs = client.get(RUNS).json()
        assert [r["run_id"] for r in runs] == [second["run"]["run_id"], first["run"]["run_id"]]
        assert runs[0]["arm"] == "harness" and runs[1]["arm"] == "bare"
        assert all(r["complete"] and r["answers"] == 6 for r in runs)
        assert set(runs[0]) >= {
            "run_id",
            "set_name",
            "set_md5",
            "model",
            "created",
            "started",
            "ended",
        }


def test_every_place_that_returns_a_run_summary_carries_the_md5_a_score_records() -> None:
    with bench_app(ScriptedArms()) as (client, _):
        job = start_run(client, "bare")
        settled(client, job["id"])
        run_id = job["run"]["run_id"]
        listed = client.get(RUNS).json()[0]
        assert listed["md5"] and len(listed["md5"]) == 32
        assert client.get(f"{RUNS}/{run_id}").json()["md5"] == listed["md5"]
        assert client.get(f"{RUNS}/{run_id}/items").json()["run"]["md5"] == listed["md5"]
        asked = post(client, SCORES, json={"set": DEMO, "runs": [run_id]})
        settled(client, asked.json()["id"])
        (scored,) = client.get(f"{SCORES}/{asked.json()['id']}").json()["summary"]["runs"]
        assert scored["md5"] == listed["md5"]


# --- the whole path in fake mode, the way the demo goes --------------------------------------


def test_the_demo_path_runs_three_arms_scores_them_and_compares_two() -> None:
    with bench_app(fake_bench_pause_s=0.0) as (client, _):
        body = {
            "set": DEMO,
            "arms": [{"name": "bare"}, {"name": "harness"}, {"name": "oracle"}],
            "models": ["fake-model"],
            "repeats": 2,
        }
        jobs = post(client, RUNS, json=body).json()
        for job in jobs:
            assert settled(client, job["id"])["state"] == "done"
        bare, harness, oracle = (j["run"]["run_id"] for j in jobs)
        asked = post(
            client,
            SCORES,
            json={
                "set": DEMO,
                "runs": [bare, harness, oracle],
                "pairs": [[bare, harness], [harness, oracle]],
                "noise": [harness, oracle],
            },
        )
        assert asked.status_code == 202 and asked.json()["kind"] == "score"
        score_id = asked.json()["score"]["score_id"]
        assert score_id == asked.json()["id"]
        assert settled(client, score_id)["state"] == "done"

        score = client.get(f"{SCORES}/{score_id}")
        assert score.status_code == 200 and score.headers["cache-control"] == "no-store"
        table = score.json()
        assert [r["run_id"] for r in table["summary"]["runs"]] == [bare, harness, oracle]
        assert [(p["base_run"], p["var_run"]) for p in table["summary"]["pairs"]] == [
            (bare, harness),
            (harness, oracle),
        ]
        means = {(g["run_id"], g["group"], g["metric"]): g["mean"] for g in table["groups"]}
        keys = [means[(r, "all", "all_keys")] for r in (bare, harness, oracle)]
        assert keys[0] < keys[1] <= keys[2]  # closed-book < harness <= oracle
        assert any(g["group"] == "lang=es" for g in table["groups"])
        all_keys = next(
            c
            for c in table["compares"]
            if c["group"] == "all" and c["metric"] == "all_keys" and c["base_run"] == bare
        )
        assert all_keys["diff"] == pytest.approx(
            means[(harness, "all", "all_keys")] - means[(bare, "all", "all_keys")]
        )
        assert all_keys["ci"][0] <= all_keys["diff"] <= all_keys["ci"][1]
        assert all_keys["up"] >= all_keys["down"] and "floor" in all_keys
        assert table["header"]["format"] == "qa-score/1"

        listed = client.get(SCORES).json()
        assert [s["score_id"] for s in listed] == [score_id]
        assert listed[0]["owner"] == "ana" and listed[0]["state"] == "done"
        items = client.get(
            f"{SCORES}/{score_id}/items", params={"run_id": harness, "limit": 200}
        ).json()
        assert items["total"] == 12 and {i["run_id"] for i in items["items"]} == {harness}
        assert items["items"][0]["metrics"]["wall_s"] is not None
        key_item = next(i for i in items["items"] if i["id"] == "DP-001" and i["repeat"] == 1)
        assert key_item["keys"] and key_item["keys"][0]["i"] == 0
        one = client.get(f"{SCORES}/{score_id}/items", params={"id": "DP-002", "limit": 200}).json()
        assert one["total"] == 6  # three runs, two repeats
        assert (
            client.get(f"{SCORES}/{score_id}/items", params={"run_id": "ghost"}).json()["items"]
            == []
        )


def test_a_score_that_does_not_exist_is_a_404_and_an_odd_id_a_422() -> None:
    with bench_app() as (client, _):
        assert client.get(f"{SCORES}/{'0' * 26}").status_code == 404
        assert client.get(f"{SCORES}/{'0' * 26}/items").status_code == 404
        assert client.get(f"{SCORES}/nope").status_code == 422
        assert client.get(SCORES).json() == []


def _with_run(value: Any, run: str) -> Any:
    """``value`` with the placeholder ``A`` replaced by the id of a run that exists."""
    if isinstance(value, dict):
        return {k: _with_run(v, run) for k, v in value.items()}
    if isinstance(value, list):
        return [_with_run(v, run) for v in value]
    return run if value == "A" else value


@pytest.mark.parametrize(
    ("change", "status", "path"),
    [
        ({"runs": []}, 422, "/runs"),
        ({"runs": ["ghost-1"]}, 422, "/runs/0"),
        ({"pairs": [["ghost-1", "ghost-2"]]}, 422, "/pairs/0"),
        ({"pairs": [["A", "A"]]}, 422, "/pairs/0"),
        ({"pairs": [["A"]]}, 422, "/pairs/0/1"),
        ({"noise": ["A", "A"]}, 422, "/noise"),
        ({"bootstrap_engine": "dice"}, 422, "/bootstrap_engine"),
        ({"set": {"name": "demo-papers", "version": "v9"}}, 404, None),
        ({"set": {"name": "demo-papers", "version": "v2"}}, 422, "/runs/0"),
        ({"surprise": 1}, 422, "/surprise"),
    ],
)
def test_a_scoring_that_cannot_be_asked_for_is_told_why(
    change: dict[str, Any], status: int, path: str | None
) -> None:
    with bench_app(ScriptedArms()) as (client, _):
        job = start_run(client)
        settled(client, job["id"])
        run = job["run"]["run_id"]
        body: dict[str, Any] = {"set": DEMO, "runs": [run]} | _with_run(change, run)
        done = post(client, SCORES, json=body)
        assert done.status_code == status, done.text
        if path is not None:
            assert path in [e["path"] for e in done.json()["errors"]], done.json()


def test_a_run_that_is_still_going_cannot_be_scored_yet() -> None:
    arms = ScriptedArms()
    gate = arms.hold("DP-002")
    with bench_app(arms) as (client, _):
        job = start_run(client)
        wait_for(client, job["id"], lambda j: j["progress"]["done"] == 2)
        done = post(client, SCORES, json={"set": DEMO, "runs": [job["run"]["run_id"]]})
        assert done.status_code == 409 and "has not ended" in done.json()["detail"]
        gate.set()
        settled(client, job["id"])
        assert (
            post(client, SCORES, json={"set": DEMO, "runs": [job["run"]["run_id"]]}).status_code
            == 202
        )


def test_deleting_is_not_offered() -> None:
    with bench_app(ScriptedArms()) as (client, _):
        job = start_run(client)
        settled(client, job["id"])
        assert delete(client, f"{JOBS}/{job['id']}").status_code == 405
        assert delete(client, f"{RUNS}/{job['run']['run_id']}").status_code == 405


# --- the size of a request ---------------------------------------------------------------------


def test_a_body_over_the_cap_is_a_413_before_anything_is_read_into_a_job() -> None:
    with bench_app() as (client, _):
        for path in (RUNS, SCORES):
            done = post(client, path, content=b'{"notes": "' + b"x" * 40_000 + b'"}')
            assert done.status_code == 413 and done.headers["content-type"].startswith(PROBLEM)
        assert client.get(JOBS).json() == []


def test_a_key_written_twice_in_a_body_is_a_400() -> None:
    with bench_app() as (client, _):
        body = '{"set": {"name": "demo-papers", "version": "v1"}, "arms": [{"name": "bare"}], '
        body += '"models": ["fake-model"], "repeats": 1, "repeats": 2}'
        done = post(
            client, RUNS, content=body.encode(), headers={"Content-Type": "application/json"}
        )
        assert done.status_code == 400 and done.headers["content-type"].startswith(PROBLEM)
        assert client.get(JOBS).json() == []


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (RUNS, {**run_body(), "models": ["m" * 129]}),
        (RUNS, {**run_body(), "models": [""]}),
        (SCORES, {"set": DEMO, "runs": ["r" * 129]}),
        (SCORES, {"set": DEMO, "runs": ["../x"]}),
        (SCORES, {"set": DEMO, "runs": ["a"], "pairs": [["a", "b" * 200]]}),
        (SCORES, {"set": DEMO, "runs": ["a", "b"], "noise": ["a", "/b"]}),
        (SCORES, {"set": DEMO, "runs": ["a"], "group_by": ["f"] * 9}),
        (SCORES, {"set": DEMO, "runs": ["a"], "group_by": ["f" * 201]}),
        (SCORES, {"set": DEMO, "runs": ["a"], "groups": ["g=1"] * 33}),
        (SCORES, {"set": DEMO, "runs": ["a"], "groups": ["g" * 401]}),
    ],
)
def test_the_lists_and_names_of_a_request_have_caps(path: str, body: dict[str, Any]) -> None:
    with bench_app() as (client, _):
        done = post(client, path, json=body)
        assert done.status_code == 422, done.text
        assert client.get(JOBS).json() == []
