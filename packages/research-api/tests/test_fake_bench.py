"""The fake arms: made-up answers that are deterministic, ordered by arm, and valid files."""

from __future__ import annotations

import asyncio
import time

import pytest
from bench_helpers import DEMO_IDS
from helpers import arun
from qa_bench.formats import QA_RUN, check_line

from research_api.adapters.fake import FakeArms, FakeModelServer
from research_api.adapters.fake.bench import OFFLINE, ONLINE
from research_api.bootstrap.demo_sets import paper_items
from research_api.domain.errors import UnavailableError, UpstreamError
from research_api.domain.runs import BARE_PROMPT_MD5, ORACLE_TIMINGS_NOTE, item_record
from research_api.domain.sets import SetRef
from research_api.ports.jobs import ArmCall

ITEMS = {i["id"]: i for i in paper_items()}
REF = SetRef("demo-papers", "v1")


def call(arm: str, item_id: str, repeat: int = 1, **extra: object) -> ArmCall:
    return ArmCall(arm, extra.get("variant"), {}, "m", ITEMS[item_id], repeat, REF)  # type: ignore[arg-type]


@arun
@pytest.mark.parametrize("arm", ["bare", "harness", "oracle"])
async def test_the_same_question_gets_the_same_answer_twice_and_another_repeat_may_differ(
    arm: str,
) -> None:
    fake = FakeArms()
    first = await fake.answer(call(arm, "DP-002"))
    again = await fake.answer(call(arm, "DP-002"))
    assert first == again
    answers = {(await fake.answer(call(arm, "DP-002", repeat=r))).answer for r in range(1, 12)}
    assert answers  # deterministic per repeat; the repeats of a hard question may disagree


@arun
@pytest.mark.parametrize("arm", ["bare", "harness", "oracle"])
async def test_every_answer_is_a_valid_qa_run_item(arm: str) -> None:
    fake = FakeArms()
    for item_id in DEMO_IDS:
        answer = await fake.answer(call(arm, item_id))
        assert check_line(QA_RUN, item_record(item_id, 1, answer), header=False) == []


@arun
async def test_a_better_arm_finds_what_a_worse_one_finds() -> None:
    fake = FakeArms()
    hits: dict[str, set[str]] = {}
    for arm in ("bare", "harness", "oracle"):
        found = set()
        for item_id, item in ITEMS.items():
            answer = (await fake.answer(call(arm, item_id))).answer or ""
            for fact in item.get("key_facts") or []:
                if str(fact["value"]) in answer:
                    found.add(f"{item_id}:{fact['value']}")
        hits[arm] = found
    assert hits["bare"] <= hits["harness"] <= hits["oracle"]
    assert len(hits["bare"]) < len(hits["oracle"])


@arun
async def test_the_oracle_gets_the_gold_passages_and_nothing_else() -> None:
    answer = await FakeArms().answer(call("oracle", "DP-002"))
    texts = [p["md_text"] for paper in ITEMS["DP-002"]["gold_papers"] for p in paper["passages"]]
    assert [c.text for c in answer.contexts] == texts and answer.citations == ()
    # named and paged like the live arm's contexts, and marked the way its items are
    assert [c.name for c in answer.contexts] == [
        f"{paper['doc_id'][:16]} pages 1-1" for paper in ITEMS["DP-002"]["gold_papers"]
    ]
    assert answer.usage is not None and answer.usage.truncated is False
    assert answer.ext == {"timings_note": ORACLE_TIMINGS_NOTE}


@arun
async def test_the_harness_cites_papers_and_the_closed_book_arm_cites_nothing() -> None:
    fake = FakeArms()
    harness = await fake.answer(call("harness", "DP-001"))
    assert harness.citations and harness.contexts and harness.timings is not None
    bare = await fake.answer(call("bare", "DP-001"))
    assert bare.citations == () and bare.contexts == () and bare.prompt_md5 == BARE_PROMPT_MD5
    assert bare.usage is not None and bare.usage.llm_requests == 1


@arun
async def test_a_question_without_key_facts_gets_a_refusal() -> None:
    answer = await FakeArms().answer(call("harness", "DP-003"))
    assert answer.answer == "I cannot answer."


@arun
async def test_words_in_a_question_make_it_fail_on_purpose() -> None:
    fake = FakeArms()

    def with_text(text: str) -> ArmCall:
        item = {**ITEMS["DP-001"], "question": text}
        return ArmCall("bare", None, {}, "m", item, 1, REF)

    with pytest.raises(UnavailableError):
        await fake.answer(with_text("please simulate-unavailable"))
    with pytest.raises(UpstreamError):
        await fake.answer(with_text("please simulate-upstream"))
    with pytest.raises(RuntimeError):
        await fake.answer(with_text("please simulate-crash"))


@arun
async def test_the_pause_is_the_time_an_answer_takes() -> None:
    started = time.perf_counter()
    answer = await FakeArms(pause_s=0.05).answer(call("bare", "DP-001"))
    assert time.perf_counter() - started >= 0.05
    assert answer.timings is not None and answer.timings.wall_s == 0.05


def test_the_fake_arms_have_no_refusal_and_no_wait() -> None:
    fake = FakeArms()
    assert fake.refusal("oracle") is None
    assert fake.prompt_md5("bare") == BARE_PROMPT_MD5 and fake.prompt_md5("harness") is None
    assert asyncio.run(fake.waiting_for("bare", "m")) is None


@arun
async def test_the_fake_models_are_one_that_answers_and_one_that_is_down() -> None:
    server = FakeModelServer()
    assert [(m.name, m.available) for m in await server.models()] == [
        (ONLINE, True),
        (OFFLINE, False),
    ]
    assert await server.get("nobody") is None and (await server.get(ONLINE)) is not None
    assert (await server.chat(ONLINE, "s", "u", max_tokens=1, temperature=0)).text
    with pytest.raises(UnavailableError):
        await server.chat(OFFLINE, "s", "u", max_tokens=1, temperature=0)
