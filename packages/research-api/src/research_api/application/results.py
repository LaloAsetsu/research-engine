"""Use cases: read runs and scores.

The files are the truth: a run is whatever ``qa-run/1`` file sits in the runs folder, whoever
made it (a job of this service, or a file a teammate dropped there), and a score is a
``qa-score/1`` file. The job table adds who started it and how far it got. A run file that is
still growing is read as far as its last whole line.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from ..domain.errors import CorruptStoreError, NotFoundError
from ..domain.jobs import Job, valid_job_id, valid_run_id
from ..domain.sets import SetRef, parse_header_name
from ..ports.jobs import BenchFiles, JobRepo, RunFormats, ScoreData, Scorer
from ..ports.sets import SetFormats
from .sets import ManageSets

log = logging.getLogger("research_api.results")

DEFAULT_PAGE = 20
MAX_PAGE = 200
SCORE_CACHE = 16
ITEM_CHECK_CACHE = 32
RUN_FILTERS = ("all", "errors", "answered")


@dataclass(frozen=True)
class _ItemCheck:
    """Which item lines of a run file were found not to follow ``qa-run/1``, and for which bytes.

    ``size`` and ``digest`` say what was read; ``count`` is how many items it held. A file that
    only grew keeps the verdicts it already has (a line never changes once it is whole), so a
    run that is still going is checked a few lines at a time, not whole at every request.
    """

    size: int
    digest: bytes
    count: int
    bad: frozenset[int]


def _digest(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=16).digest()


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    set_name: str
    set_md5: str
    arm: str
    variant: str | None
    model: str
    created: str
    started: str | None
    ended: str | None
    repeats: int | None
    answers: int
    errors: int
    expected: int | None
    complete: bool
    notes: str
    job_id: str | None = None
    owner: str | None = None
    state: str | None = None


@dataclass(frozen=True)
class RunItemView:
    id: str
    repeat: int
    question: str | None
    reference_answer: str | None
    answer: str | None
    citations: list[dict[str, Any]]
    contexts: list[dict[str, Any]]
    timings: dict[str, Any] | None
    usage: dict[str, Any] | None
    error: dict[str, Any] | None


@dataclass(frozen=True)
class RunItemsPage:
    run: RunSummary
    items: list[RunItemView]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True)
class ScoreSummary:
    score_id: str
    created: str
    set_name: str
    set_md5: str
    runs: list[dict[str, Any]]
    pairs: list[tuple[str, str]]
    records: int
    owner: str | None = None
    state: str | None = None


@dataclass(frozen=True)
class ScoreView:
    summary: ScoreSummary
    header: dict[str, Any]
    groups: list[dict[str, Any]]
    compares: list[dict[str, Any]]


@dataclass(frozen=True)
class ScoreItemsPage:
    items: list[dict[str, Any]]
    total: int
    offset: int
    limit: int


def _when(text: str) -> datetime:
    """A time written in a header as a point in time, so runs from different zones sort right."""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _page(offset: int, limit: int) -> tuple[int, int]:
    return max(offset, 0), min(max(limit, 1), MAX_PAGE)


class RunsDesk:
    def __init__(
        self,
        *,
        files: BenchFiles,
        formats: RunFormats,
        repo: JobRepo,
        sets: ManageSets,
        set_formats: SetFormats,
    ) -> None:
        self._files = files
        self._formats = formats
        self._repo = repo
        self._sets = sets
        self._set_formats = set_formats
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[tuple[int, int], RunSummary | None]] = {}
        self._checked: dict[str, _ItemCheck] = {}

    def _jobs_by_run(self) -> dict[str, Job]:
        found: dict[str, Job] = {}
        for job in self._repo.list(kind="run", limit=5000):
            if job.artifact_id:
                found.setdefault(job.artifact_id, job)  # newest first: keep the newest
        return found

    def _summary(self, run_id: str) -> RunSummary | None:
        """What the file ``run_id`` holds, or None when it is not a run this service can show.

        A run has one id, the name of its file, and the run id in its header must be the same:
        the id is what ``GET /runs/{id}`` and ``POST /scores`` open, and ``qa_bench.score`` matches
        the pairs it is given against the header's. A file whose header says another id (a copy
        under a new name, a file renamed by hand) is left out, as is a file whose header is not a
        valid ``qa-run/1`` header. The verdict is kept with the file's stamp, so a file left out is
        logged once, not at every listing.
        """
        stamp = self._files.run_stamp(run_id)
        if stamp is None:
            return None
        with self._lock:
            cached = self._cache.get(run_id)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        data = self._files.read_run(run_id)
        if data is None:
            return None
        try:
            summary = self._read_summary(run_id, data)
        except CorruptStoreError as error:
            log.warning(
                "a run file was left out",
                extra={"event": "runs.skipped", "run_id": run_id, "reason": error.detail},
            )
            summary = None
        with self._lock:
            self._cache[run_id] = (stamp, summary)
        return summary

    def _read_summary(self, run_id: str, data: bytes) -> RunSummary:
        run = self._formats.read(data)
        header = run.header
        if header.get("run_id") != run_id:
            raise CorruptStoreError("the run id in the header is not the name of the file")
        arm = header["arm"]
        errors = sum(1 for i in run.items if "error" in i)
        return RunSummary(
            run_id=run_id,
            set_name=str(header["set"]["name"]),
            set_md5=str(header["set"]["md5"]),
            arm=str(arm["name"]),
            variant=arm.get("variant"),
            model=str(header["model"]["id"]),
            created=str(header["created"]),
            started=header.get("started"),
            ended=header.get("ended"),
            repeats=header.get("repeats"),
            answers=len(run.items),
            errors=errors,
            expected=None,
            complete=header.get("ended") is not None,
            notes=str(header.get("notes", "")),
        )

    def _with_job(self, summary: RunSummary, jobs: Mapping[str, Job]) -> RunSummary:
        job = jobs.get(summary.run_id)
        if job is None:
            return summary
        return replace(
            summary,
            job_id=job.id,
            owner=job.owner,
            state=job.state.value,
            expected=job.progress.total,
        )

    def list(self) -> list[RunSummary]:
        """Every readable run, newest first."""
        jobs = self._jobs_by_run()
        found = [
            self._with_job(s, jobs)
            for run_id in self._files.run_ids()
            if (s := self._summary(run_id)) is not None
        ]
        return sorted(found, key=lambda s: (_when(s.started or s.created), s.run_id), reverse=True)

    def get(self, run_id: str) -> RunSummary:
        summary = self._summary(run_id) if valid_run_id(run_id) else None
        if summary is None:
            raise NotFoundError("No such run.")
        return self._with_job(summary, self._jobs_by_run())

    def items(
        self,
        run_id: str,
        *,
        filter: str = "all",
        item_id: str | None = None,
        offset: int = 0,
        limit: int = DEFAULT_PAGE,
        contexts: bool = True,
    ) -> RunItemsPage:
        summary = self.get(run_id)
        data = self._files.read_run(run_id)
        if data is None:
            raise NotFoundError("No such run.")
        try:
            run = self._formats.read(data)
        except CorruptStoreError:  # the file changed since the summary was made
            raise NotFoundError("No such run.") from None
        questions = self._questions(summary.set_name)
        chosen = [
            i
            for i in self._fitting(run_id, data, run.items)
            if (item_id is None or i["id"] == item_id)
            and (filter == "all" or (filter == "errors") == ("error" in i))
        ]
        offset, limit = _page(offset, limit)
        shown: list[RunItemView] = []
        for item in chosen[offset : offset + limit]:
            question = questions.get(item["id"], {})
            shown.append(
                RunItemView(
                    id=item["id"],
                    repeat=item["repeat"],
                    question=question.get("question"),
                    reference_answer=question.get("answer"),
                    answer=item.get("answer"),
                    citations=list(item.get("citations") or []),
                    contexts=list(item.get("contexts") or []) if contexts else [],
                    timings=item.get("timings"),
                    usage=item.get("usage"),
                    error=item.get("error"),
                )
            )
        return RunItemsPage(summary, shown, len(chosen), offset, limit)

    def _fitting(
        self, run_id: str, data: bytes, items: tuple[dict[str, Any], ...]
    ) -> list[dict[str, Any]]:
        """The item lines that follow ``qa-run/1``. A file dropped in by hand may hold a line with
        a wrong type (an answer that is a number, citations that are a count): it is left out
        here, as the scorer would refuse it, and does not break the page."""
        digest = _digest(data)
        with self._lock:
            known = self._checked.get(run_id)
        if known is not None and known.size == len(data) and known.digest == digest:
            check = known
        else:
            start, before = 0, frozenset[int]()
            if (
                known is not None
                and known.size < len(data)
                and known.count <= len(items)
                and _digest(data[: known.size]) == known.digest
            ):
                start, before = known.count, known.bad
            bad = before | {
                n for n in range(start, len(items)) if not self._formats.item_fits(items[n])
            }
            check = _ItemCheck(len(data), digest, len(items), bad)
            if bad != before:
                log.warning(
                    "item lines of a run file were left out",
                    extra={"event": "runs.lines_skipped", "run_id": run_id, "lines": len(bad)},
                )
            with self._lock:
                self._checked.pop(run_id, None)
                self._checked[run_id] = check
                while len(self._checked) > ITEM_CHECK_CACHE:
                    del self._checked[next(iter(self._checked))]
        return [item for n, item in enumerate(items) if n not in check.bad]

    def _questions(self, set_name: str) -> dict[str, Mapping[str, Any]]:
        """The items of the set the run is on, by id; empty when the set is gone."""
        ref: SetRef | None = parse_header_name(set_name)
        if ref is None:
            return {}
        try:
            _, data = self._sets.export(ref)
        except (NotFoundError, CorruptStoreError):
            return {}
        return {i["id"]: i for i in self._set_formats.peek(data).items}


class ScoresDesk:
    def __init__(self, *, files: BenchFiles, scorer: Scorer, repo: JobRepo) -> None:
        self._files = files
        self._scorer = scorer
        self._repo = repo
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, ScoreData] = OrderedDict()

    def _read(self, score_id: str) -> ScoreData:
        if not valid_job_id(score_id):
            raise NotFoundError("No such score.")
        with self._lock:
            cached = self._cache.get(score_id)
            if cached is not None:
                self._cache.move_to_end(score_id)
                return cached
        data = self._files.read_score(score_id)
        if data is None:
            raise NotFoundError("No such score.")
        parsed = self._scorer.read(data)  # a score file never changes once it is written
        with self._lock:
            self._cache[score_id] = parsed
            while len(self._cache) > SCORE_CACHE:
                self._cache.popitem(last=False)
        return parsed

    def _summary(self, score_id: str, data: ScoreData, job: Job | None) -> ScoreSummary:
        header = data.header
        pairs: list[tuple[str, str]] = []
        for record in data.records:
            if record.get("record") == "compare":
                pair = (record["base_run"], record["var_run"])
                if pair not in pairs:
                    pairs.append(pair)
        return ScoreSummary(
            score_id=score_id,
            created=str(header.get("created", "")),
            set_name=str(header.get("set", {}).get("name", "")),
            set_md5=str(header.get("set", {}).get("md5", "")),
            runs=list(header.get("runs", [])),
            pairs=pairs,
            records=len(data.records),
            owner=job.owner if job else None,
            state=job.state.value if job else None,
        )

    def list(self) -> list[ScoreSummary]:
        """Every readable score, newest first."""
        jobs = {
            j.artifact_id: j for j in self._repo.list(kind="score", limit=5000) if j.artifact_id
        }
        found = []
        for score_id in self._files.score_ids():
            try:
                found.append(self._summary(score_id, self._read(score_id), jobs.get(score_id)))
            except CorruptStoreError:
                continue
        return sorted(found, key=lambda s: (_when(s.created), s.score_id), reverse=True)

    def get(self, score_id: str) -> ScoreView:
        data = self._read(score_id)
        job = self._repo.get(score_id)
        return ScoreView(
            summary=self._summary(score_id, data, job),
            header=data.header,
            groups=[r for r in data.records if r.get("record") == "group"],
            compares=[r for r in data.records if r.get("record") == "compare"],
        )

    def items(
        self,
        score_id: str,
        *,
        run_id: str | None = None,
        item_id: str | None = None,
        offset: int = 0,
        limit: int = DEFAULT_PAGE,
    ) -> ScoreItemsPage:
        data = self._read(score_id)
        chosen = [
            r
            for r in data.records
            if r.get("record") == "item"
            and (run_id is None or r["run_id"] == run_id)
            and (item_id is None or r["id"] == item_id)
        ]
        offset, limit = _page(offset, limit)
        return ScoreItemsPage(chosen[offset : offset + limit], len(chosen), offset, limit)
