# Run guide

This guide takes you from a clone of the repository to a cited answer from your own corpus, on a Linux machine with NVIDIA GPUs. It runs every step of the research engine: install, the three model servers, papers in, the chunk index, and `docingest ask`. Each step says what it prints and how long it took in the measurements.

## Use downloaded documents from an explicit Linux path

The paper corpus does not need to be inside the repository. Set the input, normalized-output,
database, and configuration paths explicitly:

```bash
export RESEARCH_ENGINE_ROOT=/opt/research-engine
export RESEARCH_ENGINE_INPUT=/srv/research/papers
export RESEARCH_ENGINE_OUTPUT=/srv/research/normalized
export RESEARCH_ENGINE_DB=/srv/research/api/research.sqlite3
export RESEARCH_ENGINE_CONFIG=/srv/research/pipeline.toml

mkdir -p "$RESEARCH_ENGINE_OUTPUT" "$(dirname "$RESEARCH_ENGINE_DB")"
test -r "$RESEARCH_ENGINE_INPUT" || {
  echo "Input directory is not readable: $RESEARCH_ENGINE_INPUT" >&2
  exit 1
}
```

In the external TOML configuration, set `raw_dir` and `output_dir` to these paths. The ingestion
account needs read permission on the input directory and read/write permission on normalized
output, index files, the API database, and model caches. Linux installations without Apple
Silicon MLX should select `ocr = "openai-compatible"` and point it at a local vLLM/LM Studio or
remote OCR service.

```bash
cd "$RESEARCH_ENGINE_ROOT"
uv run docingest ingest "$RESEARCH_ENGINE_INPUT" \
  --config "$RESEARCH_ENGINE_CONFIG" --index
uv run docingest index build --config "$RESEARCH_ENGINE_CONFIG"
uv run docingest ask "What phase factors are used in the circuit?" \
  --config "$RESEARCH_ENGINE_CONFIG"
```

For containers, mount the input directory read-only and normalized output, database, index, and
model-cache directories read-write; use the same paths in the mounted configuration.

### Optional rag-anything ingestion

The multimodal adapter is intentionally separate from the default page router:

```bash
uv sync --locked --extra rag-anything
```

In the external configuration, select `multimodal = "rag-anything"` and set `[rag_anything]
output_dir` to persistent storage. Then run:

```bash
uv run docingest ingest-async "$RESEARCH_ENGINE_INPUT" \
  --config "$RESEARCH_ENGINE_CONFIG"
```

For the installed `raganything==1.4.3` and `mineru==4.0.12` combination, docingest invokes
MinerU 4's `mineru-kit parse` command directly. This avoids the legacy `mineru -p` command
constructed by rag-anything 1.4.3, which MinerU 4 rejects. Use these modes:

| Input | Configuration | Result |
|---|---|---|
| Born-digital PDF with a text layer | `tier = "flash"`, `ocr_mode = "txt"` | Fast text-layer extraction; no OCR |
| Scanned PDF or image | `tier = "standard"`, `ocr_mode = "ocr"` | Local OCR/layout parsing; requires the MinerU engine/models |

Validate one file before processing the corpus:

```bash
export PAPER="$(find "$RESEARCH_ENGINE_INPUT" -type f \( \
  -iname '*.pdf' -o -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' \
  \) -print -quit)"
export MINERU_TEST_OUTPUT="$RESEARCH_ENGINE_DATA/mineru-test"
mkdir -p "$MINERU_TEST_OUTPUT"

uv run mineru-kit parse "$PAPER" \
  -o "$MINERU_TEST_OUTPUT/parsed.md" \
  --tier flash --ocr-mode txt
```

For scanned PDFs and images, first verify the local MinerU engine and GPU:

```bash
uv run mineru-kit --help
uv run mineru-kit models --help
nvidia-smi
```

Configure the external TOML file as follows:

```toml
[rag_anything]
parser = "mineru"
method = "auto"
tier = "standard"
ocr_mode = "ocr"
output_dir = "/home/mora/ITESM_Ago_Dic_26/research-data/rag-anything"
raw_output = true
max_raw_bytes = 268435456
max_concurrency = 1
```

Run one smoke-test document:

```bash
mkdir -p "$RESEARCH_ENGINE_DATA/smoke-input"
cp "$PAPER" "$RESEARCH_ENGINE_DATA/smoke-input/"

uv run docingest ingest-async \
  "$RESEARCH_ENGINE_DATA/smoke-input" \
  --config "$RESEARCH_ENGINE_CONFIG"
```

After the smoke test succeeds, process all papers and images:

```bash
uv run docingest ingest-async \
  "$RESEARCH_ENGINE_INPUT" \
  --config "$RESEARCH_ENGINE_CONFIG"
```

The command supports PDF, Office, and image inputs, writes canonical Markdown and a manifest,
and stores bounded parser output as `rag-anything.json` beside the normalized result. The raw
output is provenance/debug data and is not indexed independently. Office inputs may require
LibreOffice:

```bash
sudo apt-get update
sudo apt-get install -y libreoffice poppler-utils ghostscript
```

`flash`/`txt` validates born-digital documents but does not OCR scanned pages. Do not process the
full corpus until the `standard`/`ocr` smoke test succeeds.

The guide links to the reference for each part instead of repeating it:

| For | Read |
|---|---|
| GPU memory, disk, the vLLM install, start times, default folders | [serving/README.md](../serving/README.md) |
| Every configuration key | [packages/docingest/config/README.md](../packages/docingest/config/README.md) |
| Every command and option | [packages/docingest/README.md](../packages/docingest/README.md#cli-overview) |
| How the index works and what it stores | [packages/docingest-index/README.md](../packages/docingest-index/README.md) and [ADR 0004](../packages/docingest/docs/adr/0004-chunk-index-and-two-stage-retrieval.md) |
| Checking a quantum circuit | [Check a circuit with quantum-sim](#check-a-circuit-with-quantum-sim) below and [packages/quantum-sim/README.md](../packages/quantum-sim/README.md) |
| Checking or converting a question set, a run, a score or a review file | [Check a question set with qa-bench](#check-a-question-set-with-qa-bench) below and [packages/qa-bench/README.md](../packages/qa-bench/README.md) |
| Scoring runs against a question set and comparing two runs | [Score runs with qa-bench](#score-runs-with-qa-bench) below and [packages/qa-bench/README.md](../packages/qa-bench/README.md#the-scorer) |

If the three model servers already run on the machine you use, do steps 1 and 2 and skip step 3, then start at step 4; [Sharing a machine](#sharing-a-machine) shows how to check them.

## Contents

- [1. Check the machine](#1-check-the-machine)
- [2. Install the workspace](#2-install-the-workspace)
- [3. Install vLLM and start the model servers](#3-install-vllm-and-start-the-model-servers)
- [4. Write your configuration](#4-write-your-configuration)
- [5. Get papers in](#5-get-papers-in)
- [6. Build the index](#6-build-the-index)
- [7. Ask](#7-ask)
- [8. Add papers later, and stop the servers](#8-add-papers-later-and-stop-the-servers)
- [Check a circuit with quantum-sim](#check-a-circuit-with-quantum-sim)
- [Check a question set with qa-bench](#check-a-question-set-with-qa-bench)
- [Score runs with qa-bench](#score-runs-with-qa-bench)
- [Measured times](#measured-times)
- [Sharing a machine](#sharing-a-machine)
- [Troubleshooting](#troubleshooting)

## 1. Check the machine

The default setup needs two GPUs with 48 GB each: one for the answering model (about 45,000 MiB in use) and one for the embedder and the reranker together (about 43,000 MiB in use). It also needs about 44 GB of disk for the model downloads, and more for the vLLM environment and caches. The details and the measurements are in [Requirements](../serving/README.md#requirements).

```bash
nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv
```

Look at which GPUs are free before you start anything. If other people use the machine, read [Sharing a machine](#sharing-a-machine) first.

What needs a GPU:

| Work | GPU |
|---|---|
| Ingesting born-digital PDFs, LaTeX sources, Markdown and text | No |
| Crawling arXiv | No |
| Ingesting scanned pages and images (OCR) | Yes, in this configuration through the answering model's server |
| Building the index | Yes, the embedder's server |
| `docingest ask` with the index | Yes, all three servers |

A machine without GPUs can still ingest and crawl. `ask` needs a model that speaks the OpenAI API, running somewhere you can reach. [`config/examples/ollama-qa.toml`](../packages/docingest/config/examples/ollama-qa.toml) shows PaperQA2 answering with Ollama models; that setup has no chunk index. The other options are in the [docingest README](../packages/docingest/README.md#paperqa2-integration).

## 2. Install the workspace

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and git. uv installs Python 3.12 if the machine has none.

```bash
git clone https://github.com/etxsA/research-engine.git
cd research-engine
uv sync --locked --all-extras
```

This installs docingest, docingest-index, quantum-sim, qa-bench and the PaperQA2 extra into one environment (`.venv` at the root). It does not install vLLM. The workspace environment is about 6 GB.

Set the Hugging Face cache once, in the shell you will use for the rest of the guide, and put it on a large disk. The model servers download into it, and docingest's `ask` loads a small embedding model from it.

```bash
export HF_HOME=/big/disk/huggingface
```

## 3. Install vLLM and start the model servers

vLLM is a separate environment, not part of the workspace. Follow [Install vLLM](../serving/README.md#install-vllm): a Python 3.12 environment with headers, `ninja` and the CUDA 12.9 build of vLLM 0.30.0, and three lines in `serving/env.local` that point `serve.sh` at it. If vLLM is already installed on the machine, you can reuse it instead; the same section says how.

Two more settings go in `serving/env.local` when they apply:

- A model that is already on disk, for example in a shared models folder, can be used without downloading it: point its role at the snapshot with `LLM_MODEL_PATH`, `EMBED_MODEL_PATH` or `RERANK_MODEL_PATH`. See [Configuration](../serving/README.md#configuration).
- `serve.sh` writes its pid files, logs and compile caches under your home by default. If your home is on a small disk, move them with `SERVING_STATE_DIR` and `SERVING_CACHE_DIR`. The same section lists the defaults and sizes.

Then start the servers, one after another, from the repository root (each command returns when its server is ready):

```bash
serving/serve.sh embed start
serving/serve.sh rerank start
serving/serve.sh llm start
```

Each command prints the model, the GPU, the port and the log file, then the time until the server answered:

```text
starting qwen-embed (qwen3-embedding-4b) on GPU 1 at http://127.0.0.1:8002 (pid N), log <state dir>/qwen-embed.log
ready after 75 s
```

The first start of each role also downloads its model, which makes it longer on a slow connection. The measured start times are in [First start](../serving/README.md#first-start): with the models already downloaded (and, for most of them, an empty compile cache), 75 to 125 s for `embed`, 80 to 100 s for `rerank` and 231 to 385 s for `llm`, so 6.5 to 10 minutes for the three. Once the compile cache is filled by an earlier start, the same commands one after another took 50 s, 55 s and 160 s: all three ready after about 4 minutes (251 s). `llm` runs on the other GPU, so it can start while `embed` and `rerank` do; that took 166 s in total (put `serving/serve.sh llm start &` before the `embed` and `rerank` commands; the `ready after` figures are rounded up to a multiple of 5 s, so one can be above the total). Check them:

```bash
serving/serve.sh embed status        # running (pid N, port 8002, model qwen3-embedding-4b)
curl -sf http://127.0.0.1:8001/health && echo llm ok
curl -sf http://127.0.0.1:8002/health && echo embed ok
curl -sf http://127.0.0.1:8003/health && echo rerank ok
```

The default GPUs are 2 for `llm` and 1 for `embed` and `rerank`. On a machine with other GPU numbers, pass them: `serving/serve.sh llm start 0 8001`. If you change a port, change it in the configuration too (step 4).

## 4. Write your configuration

[`lab-server.toml`](../packages/docingest/config/examples/lab-server.toml) already points at ports 8001 to 8003 and turns the index on. It also turns on the answer settings that the measurements in this guide use: `skip_summary = true` and `answer_max_sources = 10` in `[qa]` (the answer model reads the retrieved chunks themselves, equations included; see [Equations in answers](#equations-in-answers)). `whole_blocks` stays off, because it is optional. Copy it outside the repository and set two paths:

```bash
cp packages/docingest/config/examples/lab-server.toml "$HOME/lab.toml"
```

```toml
output_dir = "/path/to/work/normalized"   # the corpus: one folder per document

[index]
dir = "/path/to/work/index"               # the chunk indexes
```

Use absolute paths so the file works from any folder. The index folder is named after the corpus path, the embedder and the chunk settings, so set both paths once and keep them: moving the corpus starts a new, empty index. `raw_dir` (where `crawl` downloads) can stay `data/raw`, relative to the folder you run docingest from, which is git-ignored.

The rest of the guide runs from `packages/docingest` and passes the file with `-c`:

```bash
cd packages/docingest
CONFIG="$HOME/lab.toml"
```

Pass `-c "$CONFIG"` to every command. Without it, docingest uses `config/pipeline.toml`, which is the Mac setup: another corpus folder (`data/normalized` under the current folder), no index, an answering model expected on port 8080 and MLX OCR. Nothing in this guide works with it. What you see when you forget it is listed under [Troubleshooting](#troubleshooting).

## 5. Get papers in

Either crawl arXiv or ingest your own files. Both write `document.md` and `manifest.json` per paper into `output_dir`.

```bash
uv run docingest crawl 'cat:quant-ph AND ti:transmon' --limit 20 -c "$CONFIG"
uv run docingest ingest /path/to/papers -c "$CONFIG"      # PDF, scans, LaTeX, Office, Markdown; folders are walked
```

`crawl` downloads the LaTeX source of each paper (PDF if there is none) into `<raw_dir>/arxiv`, waits at least 3 seconds between requests as the arXiv terms require, and ingests what it fetched. LaTeX and born-digital PDFs need no GPU. The query syntax is in the [docingest README](../packages/docingest/README.md#arxiv-crawl). arXiv asks crawlers to identify themselves: add a `[arxiv]` table with `contact = "you@example.org"` to your configuration and the address goes into the User-Agent ([key reference](../packages/docingest/config/README.md#arxiv)). This matters most on a shared machine, where many people crawl from one address.

**Old scans take longer.** A PDF that is a scan with an invisible text layer from an old OCR program (typical for papers digitised decades ago; the page shows one image the size of the page) is read by the OCR model, not from that layer, because the model reads it with far fewer errors. That takes about 29 seconds per page on the lab GPU server (up to about 70 seconds for a dense page) where the layer took milliseconds, so a folder of 600 such pages needs about 4 to 5 hours of GPU, once: the result is cached. The first run after upgrading to a version with this rule re-processes every PDF in an existing output folder, not only these pages, because the routing keys and the PDF reader's version are part of the key of a PDF (the born-digital ones take seconds; the pages that were scanned already go to the model again). The log says `OCR (scanned page with a text layer: ...)` for these pages. To keep the old layers and skip the wait, set `scan_layer_to_ocr = false` in `[routing]` ([reference](../packages/docingest/config/README.md#routing)). Born-digital PDFs are not affected, with one exception: a page drawn over a full-page background image (a slide, a poster, a cover) with a long text is read by the model too; use `scan_layer_to_ocr = false` for such a collection.

**Math pages can go to the model too (optional, off by default).** A page with a text layer full of formulas loses their structure in that layer (fractions, scripts and sums come out as loose characters); the model keeps most of it. Set `math_to_ocr = true` in the `[routing]` table of your configuration and a page with at least `math_min_equals` (default 5) equals signs or at least `math_min_density` (default 0.04) math-symbol characters is read by the model. On 123 reference pages this lowered the math error rate from 0.122 to 0.072. It costs about 31 seconds of GPU per routed page at the default thresholds: about 12.4 hours for the reference corpus (1,446 pages), about 19 hours per 10,000 arXiv papers (only the PDF-only papers reach this path; LaTeX papers do not). It also has a risk the text layer does not: in 3 of the 17 pages the defaults route (about 1 in 6) the model changed a symbol or an index in a way that still reads as math, so check the routed pages if the formulas must be quoted. A collection in Greek should leave the rule off, because Greek letters count as math symbols. Turning the rule on re-processes every PDF in the output folder once, because the keys are part of the cache key; while it is off nothing changes and cached results stay valid. Measurements: [config reference](../packages/docingest/config/README.md#routing).

What the commands print:

- `crawl` starts with the number of records (`20 record(s) for 'cat:quant-ph AND ti:transmon'`) and then logs each record it fetched and ingested.
- `ingest` prints a line per file and, when it succeeds, where the result is: `ok -> <output_dir>/<first 16 characters of the doc id>/document.md`. A file that was ingested before with the same settings also prints a `[cache] <file> -> <folder> (config <hash>)` line before its `ok ->` line, and is not processed again.
- Both end with an `Ingestion summary` table (document, kind, pages, page methods, seconds, doc id). `crawl` adds `fetched N, ingested N`.
- A file that fails is listed in a `Failed inputs` table and the command exits with status 1 after it has processed the others.

## 6. Build the index

```bash
uv run docingest index build -c "$CONFIG"
```

This chunks every paper, sends the chunks to the embedder and commits the index. It prints one line. For 100 papers (9,404 chunks):

```text
added 100, updated 0, unchanged 0, pruned 0; embedded 9404 chunks in 200.3s
```

The time is the build's own; with Python start-up the command took 201.1 s. Run it again and nothing is embedded:

```text
added 0, updated 0, unchanged 100, pruned 0; embedded 0 chunks in 0.2s; nothing to commit
```

That took 0.90 s with start-up. A smaller example: 5 papers, 388 chunks, `embedded 388 chunks in 8.3s`.

Run one build at a time. `build` takes the lock that keeps writers apart when it starts, before it embeds anything, so a second build started at the same time stops at once with `error: another process is writing to the index <folder>; wait for it to finish`. See [Sharing a machine](#sharing-a-machine).

`index status` shows what is indexed, what is searchable and what a build would do:

```bash
uv run docingest index status -c "$CONFIG"
```

Look at the rows `indexed`, `searchable`, `corpus` and `to add`. After a complete build, `indexed` and `searchable` equal the corpus and `to add` is 0.

## 7. Ask

```bash
uv run docingest ask "What limits T1 in transmons?" -c "$CONFIG"
```

`ask` checks the index first, embeds the question, takes the 50 best chunks (dense vectors plus BM25 for English questions and for questions with LaTeX), reranks them and gives the best 10 to PaperQA2, which writes the answer. With `skip_summary = true` and `answer_max_sources = 10` in `[qa]`, as the example configuration has them, the answer model reads the 10 chunks themselves, equations included. Without them (the default) PaperQA2 first summarizes each chunk in about 100 words, and the summaries lose most LaTeX (see [Equations in answers](#equations-in-answers)). The output is the answer with its sources cited inline as `(<doc id prefix> pages N-M)`, followed by a reference list with one entry per cited page range, so a paper cited for two ranges appears twice, and the same paper often appears two to four times in one answer. To read a cited page, see [Open a cited source](#open-a-cited-source). `warning:` lines come first if the index is behind the corpus. `ask` prints no timings; measure it with `time`.

Before the answer you may also see lines that are harmless:

- a `Loading weights` progress bar: the small CPU embedding model (`sentence-transformers/all-MiniLM-L6-v2`) of PaperQA2. Only `ask` without an index (or with `--no-index`) loads it; with the index, PaperQA2 gets the chunks as given and embeds nothing. It is fetched into `HF_HOME` the first time; `uv run docingest model-path embedding` fetches it ahead of time.
- `Failed to calculate cost for qwen-local` lines: litellm has no price for a local model. There is about one per summary request, so about a dozen per question with `skip_summary = false`, and one line with `skip_summary = true`.
- on a driver that supports only CUDA 12 (such as 535), a torch `CUDA initialization: The NVIDIA driver on your system is too old` warning. The workspace's torch is a CUDA 13 build, and `ask` runs it on the CPU only. It is not related to the vLLM install.

None of them affects the answer. Two options change one question: `--no-index` answers with PaperQA2's own retrieval, and `--bm25 english|always|never` overrides the keyword setting. See [Spanish questions](#spanish-questions) for when to use the second.

### Many questions in one process

Every `docingest ask` starts Python, opens the index, copies its vectors to 32-bit floats and lists the corpus before it retrieves anything. For a script that asks many questions, `ask --stdin` does that once. It reads one JSON object per line and writes one JSON line per answer, in order:

```bash
printf '%s\n' \
  '{"id": "1", "question": "What limits T1 in transmons?"}' \
  '{"id": "2", "question": "coherencia transmon", "bm25": "never", "return_contexts": true}' \
  | uv run docingest ask --stdin -c "$CONFIG" > answers.jsonl
```

The same options as on the command line go in the line (`use_index`, `bm25`), plus `whole_blocks` (true or false, instead of the `[qa]` key, for this question) and `return_contexts` (the chunks the answer model read). Each answer line holds `answer`, `warnings`, `sources`, `timings`, the `index_generation` and an `error` that is null unless that question failed; a failed question does not stop the next one. Questions run one at a time, so the model server sees what it sees from the plain command. The index is reloaded by itself when a build commits; a change to the configuration file needs a new process. In Python, `Container(cfg).ask_session` gives the same session as an object. Nothing is stored. The fields are listed in the [command reference](../packages/docingest/src/docingest/entrypoints/README.md#docingest-ask).

### Equations in answers

By default PaperQA2 does not give the answer model the 10 chunks, it gives it a summary of about 100 words per chunk, and the summaries drop most LaTeX. For papers where the equations matter, set these keys in the `[qa]` table of your configuration. `lab-server.toml` already sets the first two:

```toml
[qa]
skip_summary = true        # the answer model reads the chunks, equations included
answer_max_sources = 10    # how many of the [index] contexts it reads; the default is 3
whole_blocks = false       # true: also complete the equation, circuit or table that a chunk cuts (see below)
```

With `skip_summary` the 10 summary requests of every question disappear and the answer prompt holds the 10 chunks as they are in the index, in the order the reranker gave them, cited as before. Because the model now reads raw circuits and equations, the answer prompt also tells it to take every circuit, gate, qubit index and equation only from the context, copied exactly with its LaTeX, to keep each one with the paper it comes from (no gate or term moved from one paper's circuit or equation into another's, and an equation number belongs to the paper it is printed in), and to say when the context does not show a gate or a term instead of guessing. The rule is fixed and needs no setting. The prompt is then about 4,000 tokens (6,100 for the 10 largest chunks of a 200-paper sample), against the 16,384 of the default `LLM_MAX_LEN` and a reply of at most 1,024. Chunks of 900 characters are far from the limit; with `chunk_chars` of about 4,000 or more, or a smaller `LLM_MAX_LEN`, the server answers HTTP 400 instead, and `ask` then asks again with half the sources (10, then 5, 2 and 1), printing `warning: answer prompt too long for the model; retried with 5 sources` at each step; if even one source does not fit, the server's error is shown. The details are in [config/README.md](../packages/docingest/config/README.md#reading-the-chunks-instead-of-summaries). Keeping `answer_max_sources = 3` is also valid and gives the 3 best chunks.

With `skip_summary` the answer prompt also holds a plain gate list right after each circuit code block of the chunks (wires numbered top to bottom, one step per drawn column: `Step 2: controlled-Z on wires 1 and 2 (control dots on both, symmetric: no target)`), because a model can read two control dots joined by a wire as a CNOT. The list is made from the block when the question is asked, so there is nothing to rebuild, no setting to turn on and the circuit code itself is unchanged; a part it cannot read is written as `unparsed`, never guessed. Circuits written inside display math get no list. The format and its limits are in [config/README.md](../packages/docingest/config/README.md#gate-lists-after-circuit-blocks).

A chunk can stop or start inside a display equation, a circuit code block or a table. To complete it when the question is asked, add `whole_blocks = true` next to `skip_summary` (it is off by default and has no effect without `skip_summary`): the rest of the block, and the caption after it, is read from the paper and added to the chunk, without repeating text that another chunk holds and up to three chunks' length (a longer block is shown as it is, with a note that it is only part). The index and the chunks it holds do not change and nothing has to be rebuilt. On the 101-question math and circuit set, 72 questions got a longer prompt; checked by hand, 3 of those answers were better, 2 were worse and 67 were the same, and the prompt grew by about 160 tokens per question on average. The details are in [config/README.md](../packages/docingest/config/README.md#blocks-that-a-chunk-cuts).

When the question names an equation number (`Eq. (2)`, `Equation 7`, `ecuación (4)`), `ask` also looks the number up in the papers it has already retrieved and adds the chunk that holds that `\tag{n}`, under a header such as `Equation (2) of paper <doc id prefix>, <title>:`, for the first two papers per number whose title shares at least four content words with the question (a question that does not name the paper in its words gets no added chunk) and at most four chunks, taking the places of the lowest-ranked chunks so the prompt keeps its size. It is made when the question is asked, with no setting and no rebuild. The details are in [config/README.md](../packages/docingest/config/README.md#equation-numbers-in-the-question).

The key has no effect with `--no-index`: `ask` prints `warning: [qa] skip_summary applies to chunks from the index ...` and summarizes as before (with `lab-server.toml`, that is the warning you see for `ask --no-index`). It changes only the answer, so the corpus and the index stay valid.

### Open a cited source

The id in a citation, `(<doc id prefix> pages N-M)`, is the first 16 characters of the document's id: the name of its folder under `output_dir`, the same one `ingest` prints after `ok ->`. The pages are the page markers of its `document.md`. For a LaTeX paper the markers number its sections, not printed pages. With a made-up id:

```bash
ID=0123456789abcdef                                  # the 16 characters from the citation
OUT=/path/to/work/normalized                         # your output_dir
grep -m1 '^# ' "$OUT/$ID/document.md"                # the title
sed -n '/^<!-- page 4 /,/^<!-- page 6 /p' "$OUT/$ID/document.md"    # pages 4 and 5; the line of page 6 ends the range
```

`$OUT/$ID/manifest.json` records where the paper came from (`source_name`, `title`, `metadata`) and, for each page, how its text was made (`method`). The reference list under the answer gives the same paper's title and authors.

## 8. Add papers later, and stop the servers

After the first build, `--index` keeps the index in step as papers arrive, with no separate build:

```bash
uv run docingest crawl 'cat:quant-ph AND ti:qubit' --limit 20 --index -c "$CONFIG"
uv run docingest ingest /path/to/more-papers --index -c "$CONFIG"
```

Each prints a line like `index: added 1, updated 0, unchanged 1; embedded 30 chunks in 0.9s` after the `Ingestion summary` table (for `crawl`, before `fetched N, ingested N`). If the index update fails, the papers stay stored and the message says to run `index build`.

Stop the servers you started when you are done. Go back to the repository root first (`cd ../..`). Each command stops only that server:

```bash
serving/serve.sh llm stop
serving/serve.sh embed stop
serving/serve.sh rerank stop
```

## Check a circuit with quantum-sim

quantum-sim simulates a quantum circuit with NumPy and says what state it prepares, what it measures, and whether two circuits are equal up to global phase. It runs on the CPU, needs no model server and no GPU, and step 2 already installed it. It reads the `circuit/1` format, which is the format of the gate lists that docingest makes from a paper's LaTeX circuits (`CircuitSteps.to_dict()`), and prints one JSON document. Run the commands from the repository root.

A circuit/1 file is an object whose `parts` is a list of parts (more than one when the drawing has an equals sign), each part a list of steps and each step a list of items, with wires numbered from 1 at the top. Write a Bell pair and run it:

```bash
cat > bell.json <<'EOF'
{"format": "circuit/1", "wires": 2,
 "parts": [[[{"kind": "gate", "label": "H", "targets": [1]}],
            [{"kind": "x", "label": "X", "targets": [2], "controls": [1]}]]]}
EOF
uv run quantum-sim run bell.json
```

The result has `"status": "ok"`, the `conventions` it used, and for each part of the circuit the `state` (amplitudes as `[re, im]`) and the `probabilities`: `"values": [0.5, 0.0, 0.0, 0.5]` for `|00>`, `|01>`, `|10>` and `|11>`. `--input 10` starts from another basis state, `--unitary` adds the matrix, and on a circuit that has a symbol (a gate labelled `R_y(\theta)`, say) `--set 'theta=\pi/2'` gives it a value; a name the circuit does not have is refused.

`check` takes a request that holds the circuit and what to compare it with: another circuit (`{"a": ..., "b": ...}`), a state or a unitary. Compare the same Bell circuit with the state it should prepare:

```bash
cat > bell-check.json <<'EOF'
{"circuit": {"format": "circuit/1", "wires": 2,
             "parts": [[[{"kind": "gate", "label": "H", "targets": [1]}],
                        [{"kind": "x", "label": "X", "targets": [2], "controls": [1]}]]]},
 "target": {"state": [0.7071067811865476, 0, 0, 0.7071067811865476]}}
EOF
uv run quantum-sim check bell-check.json
```

It prints `"equal_to_target": true` and one comparison with `"equal": true`. Both commands exit with 0 when they did the work (a check that finds the circuits different is still 0, with `"equal_to_target": false`), with 1 when the input was refused (the result says why) and with 2 when the command line does not parse.

To read a circuit from a paper, ask docingest for its gate list as data. The circuits of an ingested paper are the fenced `latex` blocks of its `document.md` (see [Open a cited source](#open-a-cited-source) for where it is), and `gate_list` reads a block's source. This writes the identity H Z H = X, drawn as one quantikz circuit with an equality sign, and checks it, because `check` compares the part left of a drawn `=` with the part right of it:

```bash
uv run python - <<'EOF' > hzh.json
import json
from docingest.domain.circuits import gate_list

source = r"""\begin{quantikz}
& \gate{H} & \gate{Z} & \gate{H} & \qw & \push{=} & \gate{X} & \qw
\end{quantikz}"""
print(json.dumps(gate_list(source).circuits[0].to_dict()))
EOF
uv run quantum-sim check hzh.json
```

The result has `"equal_to_target": true` and a comparison between `part 1` and `part 2` with `"method": "unitary"` and a `max_deviation` of about 2e-16. For a circuit of a paper, replace `source` with `circuit_code_blocks(text)[i].source`, where `text` is the `document.md` and `circuit_code_blocks` comes from the same module, and run `run` or `check` on the dictionary. Check that `gate_list(source).status` is `parsed` first. Both commands also read standard input, so the Python step can be piped straight into `uv run quantum-sim check`.

The conventions are Nielsen and Chuang: wire 1 is the top wire and the most significant bit of a basis label (the opposite of Qiskit), wires are numbered from 1, and rotations are R_a(theta) = exp(-i theta sigma_a / 2), and results are compared up to global phase at 1e-9. Every result prints them, and [packages/quantum-sim/docs/conventions.md](../packages/quantum-sim/docs/conventions.md) lists the gate table and every accepted label spelling.

Limits:

- Statevectors up to 20 wires, unitaries up to 10 wires (two circuits of 11 to 20 wires are compared on 8 random states), at most 10,000 operations, 50,000 items in a document and 32 Mi characters of input.
- Version 1 refuses what it cannot simulate: a measurement before the end of its wire, a classically controlled gate, a reset or a postselection, an angle or a gate label outside its closed grammar, and a wire legend or text that might hide one of those.
- It never skips a part to make a circuit simulable, and its own refusal is the last word: `status` `parsed` in docingest's gate list is necessary, not sufficient. A circuit that docingest read as `partial` or `unparsed` (a command or wrapper the reader does not know, a `\cw` that no measurement made classical) is refused. So is a `parsed` one whose wire legend shows a bundle, a register, a row of dots or a classical wire, and one with a ket drawn after a gate. The result is `"status": "refused"`, exit status 1, and the reasons.
- docingest does not mark a `yquant` `cbit` register as classical, so quantum-sim takes it for a qubit: compare the wire count in the result with the drawing.

The full list is in [packages/quantum-sim/README.md](../packages/quantum-sim/README.md#limits) and [the gate lists as data](../packages/docingest/src/docingest/domain/README.md#the-steps-as-data).

## Check a question set with qa-bench

qa-bench reads, checks and converts the files of the question-answer benchmark: `qset/1` (a question set), `qa-run/1` (the answers of one run), `qa-score/1` (what a scorer computed) and `reviews/1` (decisions about the items). It runs on the CPU, needs no model server and no GPU, and step 2 already installed it. Run the commands from the repository root. Each file is JSON lines whose first line names its format.

Write a set with one question and check it:

```bash
cat > my-set.qset.jsonl <<'EOF'
{"format": "qset/1", "set": "my-set/v1", "created": "2026-01-03T09:00:00+00:00"}
{"id": "Q-001", "lang": "en", "lang_origin": "native", "question": "What does the toy paper call its coupling?", "answer": "J", "form": "single", "content": "math", "corpus": "my-corpus", "gold_papers": [{"doc_id": "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1", "passages": [{"source_text": "The coupling is called J."}]}]}
EOF
uv run qa-bench validate my-set.qset.jsonl
```

It prints `ok my-set.qset.jsonl: qset/1, 1 records, md5 ...` and exits with 0. The md5 is the identity of the set: a run or a score file names the set by it, so it changes when a single character of the file changes. A file that breaks a rule is printed as `file:line: pointer: message`, one line per problem (the first 20 per file, all with `--all`), and the exit status is 1. A run, a score or a review file is checked against its set with `--set my-set.qset.jsonl`: the set name and md5 must match and every answer must be for an item of the set.

To bring an older question-set file into the format, name its shape. The shapes are `mathcirc-v1`, `circuit-tasks-v1`, `circuit-claims-v1`, `qubitqa-pilot` and `qasper`; the package README says what each one reads. This turns a tab-separated list of circuit claims into a set:

```bash
printf 'paper_id\tblock_index\tclaim_quote_short\ttarget_kind\ttarget\tinput_state\tnotes\nd4d4d4d4d4d4d4d4\t1\tH H = I\tidentity\tI (exact)\t-\tcheck\n' > claims.tsv
uv run qa-bench convert circuit-claims-v1 claims.tsv -o claims.qset.jsonl --set-name claims/v1 --corpus my-corpus
uv run qa-bench validate claims.qset.jsonl
```

The converter records the source file's name and md5 in the header of the new set. `--created 2026-01-03T09:00:00+00:00` pins the time written in the header (the default is now, in UTC), so the same input gives the same file and the same md5. `convert` does not overwrite a file that exists unless you add `--force`. `--back` writes a set made this way as a source file again; the rows are the same, but the key order and spacing of the original are not kept.

Limits:

- `validate` and `convert` check and convert files; `score` (next section) scores runs that already exist. `qa-bench` has no command to start a run yet.
- A file is read strictly. A key given twice on a line, `NaN`, `Infinity`, a number out of range, an unpaired surrogate escape (`\ud800`) and nesting deeper than 64 levels are refused.
- A shape that is not in the list is refused with the list of shapes; the command does not guess one.

## Score runs with qa-bench

`qa-bench score` reads a question set and the runs made on it and writes a `qa-score/1` file: the metrics of each answer, the mean and 95% interval of each group of questions, and, for each pair of runs you name, the paired bootstrap difference (second run minus first), its interval and how many questions went up and down. It never calls a model, needs no GPU and gives the same bytes for the same inputs.

A run points at its set by md5, so first write a set with key facts (the strings, numbers or expressions a right answer must contain) and ask `validate` for its md5:

```bash
cat > score-set.qset.jsonl <<'EOF'
{"format": "qset/1", "set": "score-demo/v1", "created": "2026-01-03T09:00:00+00:00"}
{"id": "Q-001", "lang": "en", "question": "What does the toy paper call its coupling?", "form": "single", "content": "math", "corpus": "my-corpus", "gold_papers": [{"doc_id": "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1", "passages": [{"source_text": "The coupling is called J."}]}], "key_facts": [{"type": "text", "value": "J"}]}
{"id": "Q-002", "lang": "en", "question": "How many toy modes does the paper couple?", "form": "single", "content": "math", "corpus": "my-corpus", "gold_papers": [{"doc_id": "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1", "passages": [{"source_text": "It couples 2 modes."}]}], "key_facts": [{"type": "num", "value": 2, "rel": 0.02}]}
EOF
MD5=$(uv run qa-bench validate score-set.qset.jsonl | sed 's/.*md5 //')
```

Write two runs of it by hand (a real run file comes from a runner; the package README describes the format). The first run writes the number as a word, the second as a digit:

```bash
cat > run-a.qa-run.jsonl <<EOF
{"format": "qa-run/1", "run_id": "run-a", "created": "2026-01-04T09:00:00+00:00", "set": {"name": "score-demo/v1", "md5": "$MD5"}, "arm": {"name": "harness"}, "model": {"id": "toy-model"}, "code": {"runner": "by hand"}, "corpus": {"id": "my-corpus"}}
{"id": "Q-001", "repeat": 1, "answer": "The coupling is J.", "contexts": []}
{"id": "Q-002", "repeat": 1, "answer": "It couples two modes.", "contexts": []}
EOF
cat > run-b.qa-run.jsonl <<EOF
{"format": "qa-run/1", "run_id": "run-b", "created": "2026-01-04T09:00:00+00:00", "set": {"name": "score-demo/v1", "md5": "$MD5"}, "arm": {"name": "harness"}, "model": {"id": "toy-model"}, "code": {"runner": "by hand"}, "corpus": {"id": "my-corpus"}}
{"id": "Q-001", "repeat": 1, "answer": "The coupling is J.", "contexts": []}
{"id": "Q-002", "repeat": 1, "answer": "It couples 2 modes.", "contexts": []}
EOF
uv run qa-bench score --set score-set.qset.jsonl --run run-a.qa-run.jsonl --run run-b.qa-run.jsonl \
  --pair run-a:run-b -o demo.qa-score.jsonl
uv run qa-bench validate demo.qa-score.jsonl --set score-set.qset.jsonl
```

`score` prints `wrote demo.qa-score.jsonl: qa-score/1, 43 records, md5 ...` and exits with 0. The file has one JSON object per line: the header (scorer, set md5, the runs, bootstrap settings and notes), an `item` record per answer, a `group` record per run, group and metric, and a `compare` record per pair, group and metric. Look at the comparison of the two runs:

```bash
grep '"compare"' demo.qa-score.jsonl | grep '"group": "all"' | grep '"metric": "all_keys"'
```

It shows `"base": 0.5, "var": 1.0, "diff": 0.5, ..., "up": 1, "down": 0`: the first run missed the number because it wrote "two", and a number word does not count (see the limits). With `--number-words` both runs score 1.0. The metrics ending in `_words` (`all_keys_words`, `key_share_words`, ...) always give the result with number words counted, so one table shows both.

What the options do:

- `--pair BASE:VAR` (repeat for more pairs) names runs by their `run_id`. `diff` is VAR minus BASE, `ci` its 95% bootstrap interval over questions, `up` and `down` the questions that rose and fell. `--noise A:B` names two runs of the same arm; their difference becomes the `floor` of each comparison, and `counts` is true only when the interval excludes 0 and the difference is larger than the floor.
- Groups come from the set: `all`, `lang=en`, `form=...` and every tag with 2 to 8 values (`category=a`). `--group-by lang,tag:category` picks the fields, `--group category=a|b` adds the union of two values. `--no-group-ci` skips the interval of each group, which saves time on a large set.
- `--bootstrap python_random` (the default) or `numpy_pcg64` picks the random generator of the bootstrap. The two give different last digits, and the header records which one was used. Both are written in the standard library, so numpy is not needed.
- `--created TIME` pins the time in the header (the default is now, in UTC). An output file that exists is not overwritten unless you add `--force`.

Limits:

- The questions are always taken in the order of the set file, never the order of a run file, because the bootstrap resamples positions. A run written in another order gives the same numbers.
- Number words (two, ocho) do not count for a number key unless you ask: they count wherever they stand, and a hand check found about half of them wrong. A lenient hit in a category `f` question counts only when the answer cites a gold paper.
- Not scored: reviews, multiple choice, circuit tasks and the multi-paper metrics. A run made for another set, an answer to a question the set does not have, or a pair that names an unknown run is refused with exit status 1.

## Measured times

Each row says what it was measured with. The first block is the servers, the second the commands. Where two numbers are given, they are two measurements: a lab run and a second run on a fresh install (48 GB GPUs, driver 535).

| Step | Time | Conditions |
|---|---|---|
| Start `embed`, `rerank`, `llm`, warm compile cache | 50 s, 55 s, 160 s (one after another: 251 s for the three); 55 s, 65 s, 170 s printed with `llm` started in parallel (166 s timed for the three) | models already downloaded, lab server |
| Start `embed` | 75 s and 125 s | empty compile cache in the second run, not recorded for the lab run |
| Start `rerank` | 80 s and 100 s | same |
| Start `llm` | about 231 s and 385 s | models already downloaded, empty compile cache in both; CUDA graph capture and `torch.compile` |
| Start `llm` with `LLM_EAGER=1` | about 90 s | no CUDA graphs; decoding about 3 times slower |
| `index build`, first time | 200.3 s printed, 201.1 s with start-up | 100 papers, 9,404 chunks |
| `index build`, nothing changed | 0.2 s printed, 0.90 s with start-up | same corpus |
| `index build`, small corpus | 8.3 s printed, 9.4 s with start-up | 5 papers, 388 chunks |
| `crawl --limit 5` with ingest | 32 s | 5 LaTeX sources, the 3 s spacing between requests included |
| `ask`, one question per process | 19.0 to 24.9 s, mean 21.1 s | 9 math and circuit questions on 1,249 papers (119,564 chunks), `skip_summary = true`, `answer_max_sources = 10`, 10 chunks read; program start-up included; the first question after the servers started took 21.8 s, so there is no slow first question |
| `index status` | 2.5 s | same corpus |
| One question inside the evaluation harness | 18.5 s and 21.2 s (medians) | another corpus (QASPER) and the 9,404-chunk corpus; no CLI start-up, so not comparable with the rows above |

The times of the commands depend on the papers and, for `crawl`, on arXiv.

## Sharing a machine

Several people may use one GPU machine. These rules keep your work and theirs apart:

1. **Look before you start.** `nvidia-smi` shows the memory in use per GPU. `nvidia-smi --query-compute-apps=pid,used_memory --format=csv` shows which processes hold it.
2. **Reuse running servers.** If the three servers already answer on 127.0.0.1 ports 8001, 8002 and 8003, do not start more:

   ```bash
   for port in 8001 8002 8003; do curl -sf -m 2 "http://127.0.0.1:$port/health" > /dev/null && echo "$port up" || echo "$port down"; done
   curl -s http://127.0.0.1:8001/v1/models      # the served name: qwen-local (qwen-embed on 8002, qwen-rerank on 8003)
   ```

   The served names are the ones in `lab-server.toml`, so it works unchanged. The `model` and `revision` in `[embedder]` are only recorded in the index name, not checked against the server: make sure they name what the running embedder serves, or the index will claim vectors of a model that did not make them. GPU 1 holds one embedder and one reranker; a second pair does not fit next to it.
3. **Use your own folders.** Keep your configuration file with your own `output_dir` and `[index] dir`. The pid files, logs and compile caches of `serve.sh` default to folders under your home (`SERVING_STATE_DIR`, `SERVING_CACHE_DIR`), so they are already yours. So is uv's cache unless you share it.
4. **One writer per index.** A build (and `ingest --index`, `crawl --index`) takes the index's lock when it starts, before it embeds anything. While another process holds the index, it fails at once with `error: another process is writing to the index <folder>; wait for it to finish`, and does no embedding. Start one build at a time. `ingest --index` and `crawl --index` report a held lock as `error: the index was not updated: ...`; the papers stay stored. Reading (`ask`, `index status`) is safe next to a build. Two people who share one `output_dir` and one `[index] dir` share one index folder; give each person their own unless you mean to share.
5. **Never stop other people's processes.** `serve.sh stop` ends only the servers recorded in your own state folder. Do not kill a vLLM process that you did not start. If a GPU is busy, start your server on another one (`serve.sh embed start 0 8012`) or with a lower memory fraction (`EMBED_GPU_MEM`, `RERANK_GPU_MEM`), and point your configuration at the new port: `[embedder] base_url`, `[reranker] base_url`, and for the answering model both `[ocr] base_url` and `[qa] llm_base`.

## Troubleshooting

### Model servers

- **`serve.sh` prints `already running (pid N, <model>)`.** A server recorded in your state folder is alive, so `start` does nothing and does not change its settings. Stop it first (`serve.sh <role> stop`) to restart with other settings.
- **`vLLM exited; last log lines:`.** The five lines are only the end of the log. Open the whole log; its path was printed when you started the server (`<state dir>/<served name>.log`, by default under `~/.local/state/research-engine`). Usual causes:
  - `RuntimeError: The NVIDIA driver on your system is too old (found version 12020)` near the top of the log, ending in `Engine core initialization failed`: the vLLM build does not match the driver. Reinstall as in [Install vLLM](../serving/README.md#install-vllm).
  - GPU memory: another process holds it. Check `nvidia-smi`, then lower the fraction (`LLM_GPU_MEM`, `EMBED_GPU_MEM`, `RERANK_GPU_MEM`) or use another GPU: `serving/serve.sh embed start <GPU> <PORT>`.
  - The port is taken by another server: pick another port and update the configuration.
  - A kernel compile error at start-up: missing Python headers or `ninja`. See [Install vLLM](../serving/README.md#install-vllm).
  - CUDA graph capture fails for the answering model: `LLM_EAGER=1 serving/serve.sh llm start` as a fallback (slower decoding). It has no effect if `env.local` sets `LLM_EAGER=0`, because `env.local` is read after the environment.
- **`not ready after 10 minutes, see <log>`.** The server may still be downloading or compiling. It keeps running; follow the log with `tail -f` and use `curl .../health`.
- **It downloads into the wrong folder.** Without `HF_HOME` the models go to `$SERVING_CACHE_DIR/huggingface`, not `~/.cache/huggingface`. Check that `HF_HOME` is exported in the shell that runs `serve.sh`.
- **It does not download.** Either `HF_HUB_OFFLINE=1` is set before the models are in the cache, or the machine cannot reach the Hub.
- **Odd rerank scores.** The Qwen3 reranker needs the chat template in `serving/templates/` with its exact newlines. Do not edit the template or start the reranker without it. See [templates/README.md](../serving/templates/README.md).

### Commands that talk to the servers

- **`error: embedding server http://127.0.0.1:8002/v1/embeddings unreachable after 6 attempt(s)`**, or the same for `reranker server http://127.0.0.1:8003/rerank`. The server is not running or is on another port. The command retried for about 30 seconds before it gave up. Check `serve.sh embed status` and `curl .../health`, and compare the port with `base_url` in your configuration.
- **A traceback of thousands of lines from `ask`; its last lines say `AllModelsExhaustedError` and `Connection error`.** The answering model's server (`qwen-local`, port 8001) is unreachable. Check `serve.sh llm status` and `[qa] llm_base`. During `ingest`, a scanned page fails in a different way: the file lands in the `Failed inputs` table with `OCR server http://127.0.0.1:8001/v1/... unreachable after N attempt(s)`. Check `[ocr] base_url` for that one.
- **`... answered HTTP 401 (check api_key_env)`, or 403.** The server wants a key: set the variable named in `api_key_env` of that table.

### Ingestion

- **`error: [adapters] ocr = "mlx-vlm" needs the mlx_vlm package, which cannot be imported here`**, then `stopped at <file>: the remaining inputs were not processed` (exit status 1). You ran `docingest ingest` without `-c "$CONFIG"`, or with a configuration that keeps the default OCR, which runs the model inside the process with `mlx_vlm` and exists on Apple Silicon only. Before this check existed the same mistake showed up as `ModuleNotFoundError: No module named 'mlx_vlm'` in the `Failed inputs` table, once for every scanned file (16 of 41 files in one run). Two ways out: pass `-c "$CONFIG"` with a configuration that sets `[adapters] ocr = "openai-compatible"` and `[ocr] base_url` to your OCR server (the lab example is in [`config/examples/lab-server.toml`](../packages/docingest/config/examples/lab-server.toml)), or, on a Mac, install the extra with `uv sync --all-extras` (or add `--extra mlx` to the extras you already use: `uv sync --extra mlx` alone removes the others). PDFs that have a text layer on every page never reach OCR and are ingested whichever OCR is configured, so the check only fires at the first page that needs it.
- **`error: the OCR server http://127.0.0.1:8001/v1 ([ocr] base_url ...) is not reachable`.** The OCR server is not running or is on another port. Check `serve.sh llm status` and compare the port with `[ocr] base_url`. The check is one `GET /models` with a 5 second timeout; `refused the request (HTTP 401)` means the server wants a key, set the variable named in `[ocr] api_key_env`.
- **`blank page: 0 embedded chars (<=10), 0.00000 of the pixels inked (<0.0005)` in the log, `method=blank` in `document.md`.** The page has no text and no ink, so it was not sent to OCR and is empty on purpose (OCR used to invent a page of text for it). A scanned page that you think is blank but is not reported as such has a dark border, a shadow along the spine or stains: the rule leaves it to OCR. The thresholds are the `blank_*` keys of `[routing]` ([reference](../packages/docingest/config/README.md#routing)).
- **`math-heavy page with a text layer: N '=' signs, math symbols D% of the characters` in the log, `method=vlm_ocr` for a PDF that has a text layer.** The optional math rule is on (`math_to_ocr = true` in `[routing]`) and this page reached one of its two thresholds. The thresholds are `math_min_equals` and `math_min_density`; the probe in `manifest.json` has the two measured values (`eq_signs`, `math_density`) for every page read by a version that has the rule, so you can see how close a page that stayed on its text layer came. Results cached before that version show 0 for both.
- **`scanned page with a text layer: images cover 99% of the page, N embedded chars` in the log, `method=vlm_ocr` for a PDF that has a text layer.** The page is an old scan with an OCR layer, and the rule that reads such pages with the model applied (see [Get papers in](#5-get-papers-in)). If it does not apply to a page you expect, the images cover less than `scan_image_cover` of it (the log shows `text layer` for it).
- **A contents page shows `Title 23` where the PDF has `Title . . . . . 23`.** That is intended: docingest removes the dot leaders between an entry and its page number on every PDF and image page, so the page reads the same whether the OCR model wrote them or not. Ellipses in prose and section numbers such as `1.2.3` are not touched. See [dot leaders](../packages/docingest/README.md#dot-leaders-on-contents-lines).
- **`warning: OCR output rejected (repeating text); the text layer was used`**, or `... the page was left empty`; `(more text than the page can hold)`; `(same text as page N)`. The model looped, wrote far more than a page holds, or wrote the same text for two pages, and docingest threw that output away. The manifest keeps the reason in the page's `warnings`. If the page has a usable text layer it is used, otherwise the page is empty; `docingest ingest --force` runs it again.
- **`warning: OCR stopped at the token limit (N tokens): the page may be cut off`.** A dense page (a large table) did not fit even in the larger try, which allows twice `[ocr] max_tokens` (up to 8192), or the server refused that limit (HTTP 400: its context cannot hold prompt plus 8192). Raise `[ocr] max_tokens` (and the server's context length) if you need such pages whole.

### The index

- **`error: the index is empty`.** The index was never built, or the settings now select another index folder. The folder depends on `output_dir`, the `[embedder]` `model` and `revision`, `[qa]` `chunk_chars` and `overlap`, and the chunker version, so a docingest update that changes the chunker also counts. A change to any of them starts an empty index beside the old one. Run `docingest index build`, or restore the old settings.
- **`error: the index holds N documents but none is committed`.** An earlier build was interrupted before it committed. Run `index build`.
- **`error: the index holds vectors of embedder ...`** (it goes on: `but the configured one is ...`). The folder was written with another embedder identity than the one in your `[embedder]` table. Restore those settings, or use another `[index] dir`, then run `index build`. Vectors of two models are never mixed.
- **`warning: K of N documents of the corpus are not in the index`.** You ingested papers after the last build. The answer ignores them. Run `index build`.
- **`warning: the index has changes that are not committed yet`** and **`warning: M of K retrieved chunks belong to documents that are no longer in the corpus`.** Run `index build`; it commits pending changes and prunes the papers that left the corpus.
- **`error: another process is writing to the index <folder>; wait for it to finish`.** One writer at a time; the lock is taken when a build starts: see rule 4 of [Sharing a machine](#sharing-a-machine). Wait for the other build, or find out who holds the folder.
- **`error: the index <folder> kept changing while it was being opened; ask again`.** Builds committed repeatedly while `ask` or `status` opened the index. Run the command again.
- **`error: no ingested documents`** from `index build` (it goes on: run `docingest ingest` first), or a traceback ending with the same text from `ask`. The corpus at `output_dir` is empty. Check that you passed `-c "$CONFIG"` and that `output_dir` in it is the folder you ingested into.
- **`Invalid value: --index needs [adapters] index and embedder to be set`** (exit status 2). `ingest --index` or `crawl --index` ran without `-c "$CONFIG"`, or with a configuration that has no index.
- **`ask` takes minutes, loads weights and builds the corpus every time.** It ran with `--no-index` or a configuration without `[adapters] index`, so PaperQA2 embeds the corpus on every question.

### Spanish questions

The keyword (BM25) part of the retrieval is on only for questions that look English, because in the measurements it helped English questions and added noise to Spanish ones. A Spanish question is answered from the dense ranking and the reranker, and in a test it got a cited answer in Spanish. One exception: a question that quotes LaTeX (`$\frac{a}{b}$`, `\ket{0}`) also gets the keyword part, over the words of the formula only, because the dense ranking alone misses formulas in any language. `--bm25 always` turns the keyword part on for it, `--bm25 never` turns it off for an English one. The reasons are in [ADR 0004](../packages/docingest/docs/adr/0004-chunk-index-and-two-stage-retrieval.md).
