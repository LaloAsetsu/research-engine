"""The converters: every source shape round-trips, refusals name the row, captures become run items."""

import json

import pytest
from conftest import SOURCES

from qa_bench.convert import (
    SHAPES,
    ConversionError,
    capture_to_item,
    from_qset,
    parse_citations,
    retrieval_ranks,
    to_qset,
)
from qa_bench.convert.qasper import qtype
from qa_bench.formats import QA_RUN, QSET, build, check_line, md5_hex

CREATED = "2026-01-01T00:00:00+00:00"
SOURCE_FILES = {
    "mathcirc-v1": "mathcirc.jsonl",
    "circuit-tasks-v1": "circuit-tasks.jsonl",
    "circuit-claims-v1": "circuit-claims.tsv",
    "qubitqa-pilot": "qubitqa.jsonl",
    "qasper": "qasper.jsonl",
}
CORPUS = "example-corpus"


def convert(shape, data=None, **more):
    data = (SOURCES / SOURCE_FILES[shape]).read_bytes() if data is None else data
    args = {"set_name": "converted/v1", "created": CREATED, "corpus": CORPUS}
    return to_qset(shape, data, **{**args, **more})


def test_every_shape_has_a_fixture():
    assert set(SOURCE_FILES) == set(SHAPES)


@pytest.mark.parametrize("shape", SOURCE_FILES)
def test_a_source_file_becomes_a_valid_set_with_its_source_recorded(shape):
    data = (SOURCES / SOURCE_FILES[shape]).read_bytes()
    qset = convert(shape, source_file=SOURCE_FILES[shape])
    assert qset.format == QSET
    assert qset.header["source"] == {"file": SOURCE_FILES[shape], "md5": md5_hex(data)}


@pytest.mark.parametrize("shape", SOURCE_FILES)
def test_going_back_gives_the_same_bytes_when_the_source_keeps_the_converters_key_order(shape):
    data = (SOURCES / SOURCE_FILES[shape]).read_bytes()
    assert from_qset(shape, convert(shape)) == data


def with_other_key_order(shape, data):
    """The source text with the keys of every row (the columns of a TSV) in reverse order."""
    rows = SHAPES[shape].read_rows(data.decode("utf-8"))
    flipped = [dict(reversed(row.items())) for row in rows]
    return SHAPES[shape].write_rows(flipped).encode("utf-8")


@pytest.mark.parametrize("shape", SOURCE_FILES)
def test_going_back_gives_equal_rows_even_when_the_source_orders_its_keys_differently(shape):
    data = with_other_key_order(shape, (SOURCES / SOURCE_FILES[shape]).read_bytes())
    back = from_qset(shape, convert(shape, data))
    read = SHAPES[shape].read_rows
    assert read(back.decode("utf-8")) == read(data.decode("utf-8"))
    assert back != data


@pytest.mark.parametrize("shape", SOURCE_FILES)
def test_the_same_input_gives_the_same_set_md5(shape):
    assert convert(shape).md5 == convert(shape).md5


def test_the_header_carries_the_name_the_time_and_the_notes():
    qset = convert("qasper", notes="from a test")
    assert qset.header == {
        "format": QSET,
        "set": "converted/v1",
        "created": CREATED,
        "notes": "from a test",
    }


def test_mathcirc_groups_passages_by_paper_and_keeps_the_rest_in_tags_and_ext():
    items = {i["id"]: i for i in convert("mathcirc-v1").records}
    one, two, three = items["MC-S1"], items["MC-S2"], items["MC-S3"]
    assert len(one["gold_papers"]) == 1
    assert len(one["gold_papers"][0]["passages"]) == 2
    assert [g["arxiv"] for g in three["gold_papers"]] == ["0000.00001v1", "0000.00002v1"]
    assert one["tags"] == {"category": "c", "survives": "yes", "subfield": "quant-ph"}
    assert two["tags"]["mirrors"] == "c"
    assert (one["content"], two["content"]) == ("math", "circuit")
    assert one["corpus"] == CORPUS
    assert set(one["ext"]) == {"category_name", "survives_auto", "survival_detail"}
    assert "arxiv" not in one["gold_papers"][0]["passages"][0]


def test_mathcirc_passages_out_of_paper_order_are_refused():
    rows = [json.loads(line) for line in (SOURCES / "mathcirc.jsonl").read_text().splitlines()]
    rows[2]["gold_doc_ids"].reverse()
    data = "".join(json.dumps(r) + "\n" for r in rows).encode()
    with pytest.raises(ConversionError, match="MC-S3: the passages are not grouped by paper"):
        convert("mathcirc-v1", data)


def test_circuit_tasks_map_verify_and_create():
    items = {i["id"]: i for i in convert("circuit-tasks-v1").records}
    verify, create, identity = items["CT-S1"], items["CT-S2"], items["CT-S3"]
    assert verify["form"] == "circuit_verify"
    assert verify["answer"] == "yes"
    assert verify["corpus"] == CORPUS
    assert verify["circuit"] == {"wires": 1, "global_phase": [1.0, 0.0]}
    assert verify["gold_papers"][0]["passages"] == [{"block_index": 3}]
    assert create["form"] == "circuit_create"
    assert (create["corpus"], create["gold_papers"]) == ("none", [])
    assert create["circuit"]["input_state"] == "|0>"
    assert "input_state" not in identity["circuit"]


def test_circuit_claims_get_ids_from_the_paper_and_block_and_drop_a_dash():
    items = convert("circuit-claims-v1").records
    assert [i["id"] for i in items] == ["CL-d4d4d4d4d4d4d4d4-b1", "CL-e5e5e5e5e5e5e5e5-b7"]
    assert "input_state" not in items[0]["circuit"]
    assert items[1]["circuit"]["input_state"] == "|0>"
    assert items[1]["gold_papers"][0]["passages"] == [{"block_index": 7}]
    assert "question" not in items[0]


def test_qubitqa_rows_become_choice_items_and_a_row_with_a_parent_is_a_mirror():
    items = convert("qubitqa-pilot", corpus=None).records
    native, mirror = items
    assert (native["lang_origin"], mirror["lang_origin"]) == ("native", "mirror")
    assert mirror["mirror_of"] == "qqa-s-001"
    assert native["corpus"] == "example-corpus"
    assert native["choices"]["shuffle_seed"] == 7
    assert native["writer"] == "toy-writer"
    assert native["written"] == "2026-01-02"
    assert native["gold_papers"][0]["passages"] == [{"source_text": "A synthetic evidence quote."}]


def test_an_unanswerable_qubitqa_row_is_refused_for_its_missing_search_record():
    rows = [json.loads(line) for line in (SOURCES / "qubitqa.jsonl").read_text().splitlines()]
    rows[0]["answerable"] = False
    data = "".join(json.dumps(r) + "\n" for r in rows).encode()
    with pytest.raises(
        ConversionError, match="qqa-s-001: an unanswerable row needs a search_record"
    ):
        convert("qubitqa-pilot", data, corpus=None)


def test_qasper_takes_the_first_reference_and_the_most_common_type():
    first, second = convert("qasper").records
    assert first["answer"] == "The toy baseline."
    assert first["tags"] == {"qtype": "extractive"}
    assert second["tags"] == {"qtype": "boolean"}
    assert first["gold_papers"] == []
    assert len(first["ext"]["refs"]) == 3


def test_the_qasper_type_of_a_tie_is_the_first_reference():
    assert qtype([{"type": "b"}, {"type": "a"}]) == "b"


def test_a_shape_that_needs_a_corpus_refuses_to_guess_one():
    with pytest.raises(ConversionError, match="do not name a corpus"):
        convert("mathcirc-v1", corpus=None)


def test_a_row_of_the_wrong_shape_names_the_row_and_the_missing_field():
    with pytest.raises(ConversionError, match="CT-S1: not a mathcirc-v1 row"):
        convert("mathcirc-v1", (SOURCES / "circuit-tasks.jsonl").read_bytes())
    with pytest.raises(ConversionError, match="not a circuit-tasks-v1 row"):
        convert("circuit-tasks-v1", (SOURCES / "mathcirc.jsonl").read_bytes())
    with pytest.raises(ConversionError, match="not a circuit-claims-v1 row"):
        convert("circuit-claims-v1", (SOURCES / "qasper.jsonl").read_bytes())


def test_bad_input_is_a_conversion_error_not_a_crash():
    with pytest.raises(ConversionError, match="unknown source shape"):
        to_qset("nope", b"", set_name="s", created=CREATED)
    with pytest.raises(ConversionError, match="not UTF-8"):
        convert("qasper", b"\xff\xfe")
    with pytest.raises(ConversionError, match=r"line 1.*not valid JSON"):
        convert("qasper", b"{broken")
    with pytest.raises(ConversionError, match="expected a JSON object"):
        convert("qasper", b"[1]\n")


def test_a_source_row_with_an_unpaired_surrogate_is_a_conversion_error_with_its_line():
    rows = (SOURCES / "qasper.jsonl").read_text().splitlines()
    rows[1] = rows[1].replace('"answer": "Yes"', '"answer": "Y\\ud800"')
    data = ("\n".join(rows) + "\n").encode()
    with pytest.raises(ConversionError, match=r"line 2.*unpaired surrogate"):
        convert("qasper", data)


def test_a_converted_item_that_the_schema_refuses_is_a_conversion_error():
    rows = [json.loads(line) for line in (SOURCES / "qasper.jsonl").read_text().splitlines()]
    rows[0]["lang"] = "fr"
    data = "".join(json.dumps(r) + "\n" for r in rows).encode()
    with pytest.raises(ConversionError, match="converted set is invalid"):
        convert("qasper", data)


def test_an_item_not_made_from_a_shape_is_refused_going_back():
    qset = convert("qasper")
    with pytest.raises(ConversionError, match="not an item made from a mathcirc-v1 row"):
        from_qset("mathcirc-v1", qset)


def test_the_empty_claims_file_round_trips_to_nothing():
    assert (
        from_qset(
            "circuit-claims-v1", build(QSET, {"format": QSET, "set": "s", "created": CREATED}, [])
        )
        == b""
    )


# ---- answer captures


def captures():
    return [json.loads(line) for line in (SOURCES / "ask-capture.jsonl").read_text().splitlines()]


A, B = "a1" * 32, "b2" * 32


def test_the_references_list_becomes_citations_with_full_ids():
    text = "Answer.\n\nReferences\n\n1. (a1a1a1a1a1a1a1a1 pages 2-3): A.pdf\n2. (ffffffffffffffff pages 1-1): F.pdf\n"
    assert parse_citations(text, [A, B]) == [
        {"key": "a1a1a1a1a1a1a1a1 pages 2-3", "doc_id": A, "first_page": 2, "last_page": 3},
        {"key": "ffffffffffffffff pages 1-1", "doc_id": "f" * 16, "first_page": 1, "last_page": 1},
    ]
    assert parse_citations("No references here.") == []
    assert parse_citations("") == []


def test_a_capture_becomes_a_valid_run_item():
    item = capture_to_item(captures()[0], {"id": "EX-001", "hybrid_paper": 1, "dense_paper": None})
    assert check_line(QA_RUN, item, header=False) == []
    assert item["id"] == "EX-001"
    assert item["answer"].startswith("Synthetic answer")
    assert [c["doc_id"] for c in item["citations"]] == [A, B]
    assert item["contexts"] == [
        {
            "doc_id": A,
            "name": "A-example.pdf",
            "start": 120,
            "first_page": 2,
            "last_page": 3,
            "is_reference": False,
            "text": "The coupling is J.",
        }
    ]
    assert item["ext"]["given"] == [
        {"doc_id": A, "name": "A-example.pdf", "start": 120, "text": "The coupling is J."}
    ]
    assert item["ext"]["retrieval"]["hybrid_paper"] == 1
    assert len(item["ext"]["retrieval"]) == 12
    assert item["answer_prompt_md5"] == md5_hex(b"Answer using the context.\n\nQuestion?")
    assert item["timings"] == {
        "wall_s": 21.25,
        "retrieve_s": 2.0,
        "rerank_s": 0.5,
        "answer_s": 18.0,
    }
    assert item["usage"]["completion_tokens"] == 1024
    assert item["usage"]["truncated"] is True
    assert "error" not in item


def test_candidates_are_in_reranker_order_with_ties_by_position():
    item = capture_to_item(captures()[0])
    assert [(c["doc_id"], c["start"]) for c in item["candidates"]] == [(B, 40), (A, 120), (B, 80)]
    assert item["ext"]["retrieval"] is None


def test_truncation_follows_the_token_limit_given():
    assert capture_to_item(captures()[0], max_tokens=2048)["usage"]["truncated"] is False


def test_a_failed_command_and_a_context_error_are_errors_of_the_item():
    failed = capture_to_item(captures()[1])
    assert failed["error"] == {
        "kind": "exit",
        "exit_code": 1,
        "message": "Traceback: the command failed",
    }
    too_long = capture_to_item(captures()[2])
    assert too_long["error"]["kind"] == "http_400"


def test_a_record_that_is_not_a_capture_is_refused_with_its_id():
    with pytest.raises(ConversionError, match="EX-9: not an ask capture record"):
        capture_to_item({"qid": "EX-9"})


def test_a_missing_retrieval_record_gives_twelve_nones():
    assert set(retrieval_ranks({}).values()) == {None}
    assert len(retrieval_ranks({})) == 12
