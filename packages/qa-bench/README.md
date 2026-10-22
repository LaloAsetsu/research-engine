# qa-bench

The shared formats of the question-answer benchmark and the tools that read, write, check and convert them. Four JSON-lines formats carry a benchmark from the questions to the numbers: `qset/1` (a question set), `qa-run/1` (the answers of one run), `qa-score/1` (what a scorer computed from runs) and `reviews/1` (human and checker decisions). The package depends on `jsonschema` only; it does not import docingest, quantum-sim, numpy or an HTTP client, and none of them imports it.

**Status: the formats and their tools.** Schemas, strict readers and deterministic writers, the rules between files, applying reviews to a set, converters of older question sets and answer captures, and the `qa-bench` command (`validate` and `convert`) are implemented and tested on synthetic data. The scorer and the runner are separate changes.

## Contents

- [Use it](#use-it)
- [The formats](#the-formats)
- [Rules the schemas cannot show](#rules-the-schemas-cannot-show)
- [Reviews](#reviews)
- [Converters](#converters)
- [The commands](#the-commands)
- [Code map](#code-map)
- [Tests and checks](#tests-and-checks)

## Use it

```python
from qa_bench.formats import check_against_set, read_qset, read_run, write

qset = read_qset("my-set.qset.jsonl")          # FormatError with every problem if it breaks a rule
run = read_run("my-run.qa-run.jsonl.gz")       # plain or gzip
check_against_set(run, qset)                   # [] when the run belongs to this set file
md5 = write("copy.qset.jsonl", qset)          # the md5 of the bytes written: the set's identity
```

| Function | Returns |
|---|---|
| `load(path, expected=None)`, `load_bytes(data, ...)`, `parse(text, ...)` | a `Loaded` with `.file` (a `FormatFile`) and `.problems`; nothing is raised for bad content |
| `read(path, expected=None)`, `read_qset`, `read_run`, `read_score`, `read_reviews` | a `FormatFile` (`format`, `header`, `records`, `md5`) or a `FormatError` that carries every `Problem` |
| `build(format, header, records)` | a `FormatFile` made in memory, after every line and every file rule has been checked |
| `write(path, file)` | writes the file (gzip when the name ends in `.gz`, with no time stamp) and returns the md5 of the uncompressed bytes it wrote |
| `check_line(format, record, header=...)` | the problems of one line against the JSON Schema (`line`, JSON pointer `path`, `message`) |
| `check_against_set(file, qset)` | what a run, score or reviews file gets wrong about its set |
| `qa_bench.reviews.apply_reviews`, `apply_to_set` | the items after the latest decision of each, and a new set version |
| `qa_bench.convert.to_qset`, `from_qset` | an older set file as a valid `qset/1` file, and back |
| `qa_bench.convert.capture_to_item`, `parse_citations` | an ask capture record as a `qa-run/1` item; the References list as citations |

## The formats

Each format is one JSON object per line. Line 1 is the header and the only line with `format`. The JSON Schemas (draft 2020-12) are package data in [`src/qa_bench/formats/schemas/`](src/qa_bench/formats/schemas); `load_schema("qset/1")` returns one.

| Format | File | Line 1 | Other lines |
|---|---|---|---|
| `qset/1` | one question set | `set`, `created`, `source`, `notes` | one item per question: `id`, `lang`, `form`, `corpus`, `gold_papers`, key facts, circuit, tags, ... |
| `qa-run/1` | one run: one set, one arm (`bare`, `harness`, `oracle`, `agentv1`), one model | what is needed to rerun it | one answer per `(id, repeat)`: answer, citations, contexts, candidates, timings, usage, error |
| `qa-score/1` | what a scorer computed | scorer, set, runs, bootstrap settings | `record: item`, `group` or `compare` |
| `reviews/1` | decisions, append only | `set`, `created` | `target: item` (accept, edit, reject) or `answer` (right, partial, wrong, not_attempted) |

Shared conventions:

- Ids match `[A-Za-z0-9][A-Za-z0-9._-]*`. A doc id is docingest's sha256 (64 hex); a 16-hex prefix is accepted where a source only has that.
- Dates are ISO 8601 with an offset (`2026-01-03T09:00:00+00:00` or `Z`); the check is written in the package, so it does not depend on which optional packages are installed.
- Unknown fields are refused. The open objects are `ext` (fields only one source set has), `tags`, `metrics`, `seeds`, `arm.overrides` and a review's `edit`.
- Within `/1` only optional fields are added; anything else makes `/2` with a converter.
- The md5 of a set or a run is the md5 of its text, never of the gzip bytes. A run points at its set by that md5. A file read keeps the md5 of the text it was read from; `write` returns the md5 of the text it wrote.
- A file is read strictly: a key given twice on a line, `NaN`, `Infinity`, a number too large for a float (`1e400`), an unpaired surrogate escape (`\ud800`, which UTF-8 cannot hold) and arrays or objects nested more than 64 levels deep (the same limit on every machine) are refused, so a file that passes the check can always be written, and a blank line is skipped. Lines end at a line feed only, so U+2028, U+2029 and U+0085 inside a string are text. Writing uses `ensure_ascii=False` and the default separators. A file already in that form is written back byte for byte, with the same md5. A file in another form (CRLF line ends, other spacing, `\u` escapes, blank lines) is written in that form, so it gets a new md5; use the md5 that `write` returns.

## Rules the schemas cannot show

A schema sees one line. `load` and `build` also check these:

| Format | Rule |
|---|---|
| `qset/1` | ids are unique; `mirror_of` names an item of the file |
| `qa-run/1` | `(id, repeat)` is unique; a `bare` run has no contexts |
| `qa-score/1` | every run named by a record, and by `noise_floor`, is in the header's `runs`; no record appears twice |
| `reviews/1` | none beyond the schema |

`check_against_set(file, qset)` adds the rules between files:

| File | Rule |
|---|---|
| `qa-run/1` | `set.name` and `set.md5` are the set's; every answer is for an item of the set; an `oracle` answer's contexts are the gold passages (`md_text`, in set order) |
| `qa-score/1` | `set.name` and `set.md5` are the set's; every `item` record is for an item of the set |
| `reviews/1` | `set` is the set's name; every decision names an item of the set |

## Reviews

`apply_reviews(items, lines, set_md5=None, roles=("human",))` follows the rule of `reviews/1`: for each item the decision with the latest `date` holds (dates compare as instants), and on equal dates the later line in the file. `accept` sets the item's `reviewer`, `edit` replaces the named fields and sets `reviewer`, `reject` drops the item. The result lists what was accepted, edited, rejected and not reviewed, the decisions made on another set md5 (`stale`, still applied) and the decisions about ids the set does not have (`unknown`). An edit that names no fields (the schema allows an `edit` line without an `edit` object) or leaves an item `qset/1` refuses raises `ReviewError`.

Only lines with `role: human` count unless `roles` says otherwise: an independent agent's check does not make an item gold and does not drop it. `apply_to_set(qset, reviews, created=..., set_name=...)` gives the new version as a valid `qset/1` file; its header notes name the set it came from by md5 and the counts, among them the decisions made on another version of the set. Rejecting an item that a mirror names is refused, because the result would break the `mirror_of` rule. Whether an item has the two different human accepts that make it gold is a question for the caller; this function does not compute it.

## Converters

The older question-set files are read by shape. `to_qset(shape, data, set_name=..., created=..., corpus=..., source_file=...)` returns a valid `qset/1` file whose header records the source file name and md5; `from_qset(shape, qset)` gives back the rows of the source file.

| Shape | Source | Notes |
|---|---|---|
| `mathcirc-v1` | JSON lines, one gold paper per question | passages are grouped by paper; category, circuit kind, survival, subfield and mirrored category become `tags`; the rest goes to `ext`; `corpus` is required |
| `circuit-tasks-v1` | JSON lines, `kind` `verify` or `create` | `circuit_verify` (about a block of a paper) or `circuit_create` (corpus `none`, no gold paper) |
| `circuit-claims-v1` | TSV | `circuit_claim`; the id is `CL-<paper_id>-b<block_index>`; an `input_state` of `-` is left out |
| `qubitqa-pilot` | JSON lines, multiple choice | `choices`; a row with a parent is a mirror; the row names its corpus; an unanswerable row is refused for lack of a `search_record` |
| `qasper` | JSON lines, several reference answers | the first reference is the `answer`, all of them stay in `ext.refs`; no gold papers (the source has an arXiv id, the format needs a doc id) |

Every shape round-trips: reading the converted set back gives the same rows (equal as JSON objects, or as TSV cells). The bytes are the same only when the source writes its keys in the order the converter writes them; the key order, the spacing and the spelling of the source are not kept. `corpus` is an argument, not a built-in: the converters name no corpus of their own. A row that does not fit its shape raises `ConversionError` with the row id.

Answer captures: `capture_to_item(record, retrieval=None, max_tokens=1024)` turns one record of the ask capture script (the answer, the formatted answer with its References list, the contexts the answer model got, the reranked candidates, timings, server counters, the answer prompt) into a `qa-run/1` item. It keeps what the scorers read; what the format has no field for goes to `ext` (`given`, `answer_prompt`, `llm_calls`, `retrieval`). `parse_citations(text, full_ids)` reads `N. (<16 hex> pages A-B)` entries and maps each to the full doc id. The converters of the other captures (QASPER arms, the smoke test, stripped captures) were one-off scripts tied to files on a server and are not part of the package.

## The commands

```bash
qa-bench validate FILE... [--set QSET] [--format FORMAT] [--all]
qa-bench convert SHAPE INPUT -o OUTPUT --set-name NAME [--corpus ID] [--created TIME] [--notes TEXT] [--back] [--force]
qa-bench convert ask-capture INPUT -o OUTPUT --header FILE [--retrieval FILE] [--force]
```

`validate` takes the format from line 1 (`--format` requires one), reads gzip by its first bytes, prints `ok <file>: <format>, <n> records, md5 <md5>` for each good file and `<file>:<line>: <pointer>: <message>` for each problem (the first 20 per file, all with `--all`). With `--set` it also checks runs, scores and reviews against the set file. `convert SHAPE` writes a `qset/1` file; `--created` pins the header time (default: now, in UTC) so a conversion can be repeated byte for byte; `--back` reads a `qset/1` file made from `SHAPE` and writes its rows again as a source file. An output file that exists is not overwritten unless `--force` is given. `convert ask-capture` writes a `qa-run/1` file (gzip when the name ends in `.gz`) from capture records under the run header in `--header` (a JSON file), with `--retrieval` giving the retrieval rank records by `id`. The shapes are `mathcirc-v1`, `circuit-tasks-v1`, `circuit-claims-v1`, `qubitqa-pilot` and `qasper`.

Exit status: 0 when the work was done, 1 when a file or a row was refused (the reasons are printed), 2 for a command line that does not parse.

## Code map

| Path | What |
|---|---|
| `src/qa_bench/formats/` | `schemas/` (the four JSON Schemas), `schema.py` (check one line), `jsonl.py` (strict read, deterministic write, md5, gzip), `files.py` (`FormatFile`, `load`, `read`, `build`, `write`, the rules inside a file), `crosscheck.py` (the rules between files), `problems.py` |
| `src/qa_bench/reviews.py` | `apply_reviews`, `apply_to_set`, `latest_decisions` |
| `src/qa_bench/convert/` | one module per source shape, `captures.py`, and `shapes.py` (the table and the whole-file functions) |
| `src/qa_bench/cli.py` | the `qa-bench` command |
| `tests/` | one module per area; `fixtures/` holds synthetic files only |

Import contracts (`pyproject.toml`): the layers are `cli`, then `convert` and `reviews` (independent of each other), then `formats`; those three use the standard library and `jsonschema` only (no `httpx`, `numpy`, `docingest`, `docingest_index`, `quantum_sim`); `qa_bench` never imports `research_api`, `docingest` or `quantum_sim`. The other directions are contracts in the `pyproject.toml` of docingest, docingest-index and quantum-sim.

## Tests and checks

```bash
./scripts/check.sh    # ruff, ruff format, import-linter, pyright, pytest with a coverage floor of 85%
```

The fixtures under `tests/fixtures/` are made up (fake doc ids, invented questions and reviewers): no private question set, run or answer is in this repository. The tests cover every fixture line against the schemas, about thirty broken lines that must be refused, the file rules, gzip and md5 identity, reviews, the round trip of every source shape, the capture converter and the command.
