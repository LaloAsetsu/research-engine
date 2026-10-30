"""HTTP schemas of the benchmark: jobs, runs, scores and models.

A run item is a ``qa-run/1`` item and a score record a ``qa-score/1`` record, shown as the formats
have them (plus the question and reference answer the run was asked, for the side-by-side view).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ...application.jobs import JobView, RunRequest, ScoreRequest
from ...application.results import (
    RunItemsPage,
    RunItemView,
    RunSummary,
    ScoreItemsPage,
    ScoreSummary,
    ScoreView,
)
from ...domain.jobs import (
    ARMS,
    JOB_ID_PATTERN,
    MAX_ARMS,
    MAX_MODELS,
    MAX_NOTES,
    MAX_PAIRS,
    MAX_REPEATS,
    MAX_SCORE_RUNS,
    MAX_VARIANT,
    RUN_ID_PATTERN,
    ArmSpec,
    JobKind,
    JobState,
    RunParams,
    ScoreOptions,
    ScoreParams,
    arm_spec,
)
from ...domain.sets import SetRef
from ...ports.models import ModelInfo
from .set_schemas import NAME_REGEX, VERSION_REGEX

RUN_ID_REGEX = f"^{RUN_ID_PATTERN}$"
JOB_ID_REGEX = f"^{JOB_ID_PATTERN}$"
MAX_BODY_BYTES = 32 * 1024
"""The most a run or scoring request may hold. Each field below has its own caps too."""
MAX_GROUP_FIELDS = 8
MAX_GROUPS = 32
MAX_TEXT = 200
ArmName = Literal["bare", "harness", "oracle"]
ModelName = Annotated[str, StringConstraints(min_length=1, max_length=128)]
RunIdIn = Annotated[str, StringConstraints(pattern=RUN_ID_REGEX)]
FieldName = Annotated[str, StringConstraints(min_length=1, max_length=MAX_TEXT)]
GroupText = Annotated[str, StringConstraints(min_length=1, max_length=2 * MAX_TEXT)]
Engine = Literal["python_random", "numpy_pcg64"]
assert set(ARMS) == {"bare", "harness", "oracle"}


class _Body(BaseModel):
    """A request body: a key the API does not know is an error, not ignored."""

    model_config = ConfigDict(extra="forbid")


class SetRefIn(_Body):
    name: str = Field(pattern=NAME_REGEX, max_length=64)
    version: str = Field(pattern=VERSION_REGEX, examples=["v1"])

    def to_domain(self) -> SetRef:
        return SetRef(self.name, self.version)


class ArmIn(_Body):
    name: ArmName = Field(
        description="`bare`: the model alone, no papers (the closed-book prompt). `harness`: "
        "through docingest, as an ask. `oracle`: the model gets the gold passages of each "
        "question (`md_text`, in set order) and nothing else; a set with a gold passage that "
        "has no text is a `422` at `/arms/{i}`, and a question with no gold paper gets no "
        "passage."
    )
    variant: str | None = Field(
        default=None, min_length=1, max_length=MAX_VARIANT, description="A label for the run."
    )
    overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="Harness only: `use_index` (bool), `whole_blocks` (bool), `bm25` "
        "(`english`, `always` or `never`) for every question of the run.",
    )


class RunRequestBody(_Body):
    set: SetRefIn
    arms: list[ArmIn] = Field(min_length=1, max_length=MAX_ARMS)
    models: list[ModelName] = Field(
        min_length=1,
        max_length=MAX_MODELS,
        description="Names from `GET /models`. One job is made for each arm and model. The "
        "`harness` and `oracle` arms answer with docingest's own model and accept only that one "
        "(a `422` at `/arms/{i}` names it); `bare` takes any.",
    )
    repeats: int = Field(default=1, ge=1, le=MAX_REPEATS)
    notes: str = Field(default="", max_length=MAX_NOTES)

    def to_domain(self) -> RunRequest:
        arms = tuple(
            arm_spec(a.name, a.variant, a.overrides, at=f"/arms/{i}")
            for i, a in enumerate(self.arms)
        )
        return RunRequest(self.set.to_domain(), arms, tuple(self.models), self.repeats, self.notes)


class ScoreRequestBody(_Body):
    set: SetRefIn
    runs: list[RunIdIn] = Field(
        min_length=1,
        max_length=MAX_SCORE_RUNS,
        description="Run ids, all on this set version.",
    )
    pairs: list[tuple[RunIdIn, RunIdIn]] = Field(
        default_factory=list,
        max_length=MAX_PAIRS,
        description="`[base, variant]`: the paired bootstrap of variant minus base.",
    )
    noise: tuple[RunIdIn, RunIdIn] | None = Field(
        default=None,
        description="Two runs of the same arm: their differences are the noise floor.",
    )
    group_by: list[FieldName] | None = Field(
        default=None,
        max_length=MAX_GROUP_FIELDS,
        description="Set fields (or `tag:NAME`) that split the questions into groups.",
    )
    groups: list[GroupText] = Field(
        default_factory=list,
        max_length=MAX_GROUPS,
        description="Unions written `label=v1|v2`.",
    )
    bootstrap_engine: Engine = "python_random"
    number_words: bool = False

    def to_domain(self) -> ScoreRequest:
        return ScoreRequest(
            self.set.to_domain(),
            tuple(self.runs),
            ScoreOptions(
                pairs=tuple(self.pairs),
                noise=self.noise,
                group_by=tuple(self.group_by) if self.group_by is not None else None,
                groups=tuple(self.groups),
                engine=self.bootstrap_engine,
                number_words=self.number_words,
            ),
        )


class SetRefOut(BaseModel):
    name: str
    version: str


class ProgressOut(BaseModel):
    done: int
    total: int


class JobErrorOut(BaseModel):
    kind: str
    message: str


class ArmOut(BaseModel):
    name: ArmName
    variant: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_domain(cls, arm: ArmSpec) -> ArmOut:
        return cls(name=arm.name, variant=arm.variant, overrides=dict(arm.overrides))  # type: ignore[arg-type]


class RunJobOut(BaseModel):
    """What a run job asks for."""

    run_id: str = Field(
        description="The `qa-run/1` file it writes (it exists once the job starts)."
    )
    arm: ArmOut
    model: str
    repeats: int
    notes: str


class ScoreJobOut(BaseModel):
    score_id: str = Field(description="The `qa-score/1` file it writes.")
    runs: list[str]
    pairs: list[tuple[str, str]]


class JobOut(BaseModel):
    id: str
    kind: JobKind
    state: JobState
    settled: bool = Field(
        description="True when the job is not going to change by itself (a cancelled or "
        "interrupted job waits for a resume): the client can stop polling."
    )
    owner: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    progress: ProgressOut
    set: SetRefOut
    run: RunJobOut | None = None
    score: ScoreJobOut | None = None
    error: JobErrorOut | None = None
    position: int | None = Field(
        default=None,
        description="A queued run: how many jobs are ahead of it (the running one counts).",
    )
    waiting_for: str | None = Field(
        default=None,
        description="A queued run that is first but has not started: why (a model server that "
        "does not answer). Fixed text.",
    )

    @classmethod
    def from_domain(cls, view: JobView) -> JobOut:
        job = view.job
        params = job.params
        run = score = None
        if isinstance(params, RunParams):
            assert job.artifact_id is not None
            run = RunJobOut(
                run_id=job.artifact_id,
                arm=ArmOut.from_domain(params.arm),
                model=params.model,
                repeats=params.repeats,
                notes=params.notes,
            )
        elif isinstance(params, ScoreParams):
            score = ScoreJobOut(
                score_id=job.id,
                runs=list(params.runs),
                pairs=list(params.options.pairs),
            )
        return cls(
            id=job.id,
            kind=job.kind,
            state=job.state,
            settled=job.settled,
            owner=job.owner,
            created_at=job.created_at,
            started_at=job.started_at,
            ended_at=job.ended_at,
            progress=ProgressOut(done=job.progress.done, total=job.progress.total),
            set=SetRefOut(name=params.set.name, version=params.set.version),
            run=run,
            score=score,
            error=JobErrorOut(kind=job.error.kind, message=job.error.message)
            if job.error
            else None,
            position=view.position,
            waiting_for=view.waiting_for,
        )


class ModelOut(BaseModel):
    name: str = Field(description="The name to send in a run request.")
    label: str
    revision: str | None = None
    available: bool = Field(description="Whether its server answered a moment ago.")

    @classmethod
    def from_domain(cls, m: ModelInfo) -> ModelOut:
        return cls(name=m.name, label=m.label, revision=m.revision, available=m.available)


class RunSummaryOut(BaseModel):
    run_id: str
    set_name: str = Field(description="The set as its header names it: `name/version`.")
    set_md5: str
    arm: str
    variant: str | None = None
    model: str
    created: str
    started: str | None = None
    ended: str | None = Field(default=None, description="Only a run that went to its end has it.")
    repeats: int | None = None
    answers: int = Field(description="Answer lines in the file, errors included.")
    errors: int = Field(description="Of those, how many are an error and not an answer.")
    expected: int | None = Field(
        default=None, description="Questions times repeats, when a job of this service made it."
    )
    complete: bool
    notes: str
    md5: str | None = Field(
        default=None,
        description="The md5 of the run file's bytes as read (unzipped when gzip): the value "
        "`ScoreRunOut.md5` records when the run is scored, so a score is of this version of the "
        "run when the two are equal.",
    )
    job_id: str | None = None
    owner: str | None = None
    state: JobState | None = None

    @classmethod
    def from_domain(cls, s: RunSummary) -> RunSummaryOut:
        return cls.model_validate(s, from_attributes=True)


class RunCitationOut(BaseModel):
    key: str
    doc_id: str
    first_page: int | None = None
    last_page: int | None = None


class RunContextOut(BaseModel):
    doc_id: str
    name: str | None = None
    start: int | None = None
    first_page: int | None = None
    last_page: int | None = None
    is_reference: bool | None = None
    score: float | None = None
    text: str | None = None


class RunTimingsOut(BaseModel):
    wall_s: float | None = None
    retrieve_s: float | None = None
    rerank_s: float | None = None
    answer_s: float | None = None


class RunUsageOut(BaseModel):
    llm_requests: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    rerank_requests: int | None = None
    retries: int | None = None
    truncated: bool | None = None


class RunItemErrorOut(BaseModel):
    kind: str
    exit_code: int | None = None
    message: str | None = None


class RunItemOut(BaseModel):
    id: str
    repeat: int
    question: str | None = Field(description="From the set; null when the set is gone.")
    reference_answer: str | None = None
    answer: str | None
    citations: list[RunCitationOut]
    contexts: list[RunContextOut]
    timings: RunTimingsOut | None = None
    usage: RunUsageOut | None = None
    error: RunItemErrorOut | None = None

    @classmethod
    def from_domain(cls, item: RunItemView) -> RunItemOut:
        return cls.model_validate(item, from_attributes=True)


class RunItemsOut(BaseModel):
    run: RunSummaryOut
    items: list[RunItemOut]
    total: int
    offset: int
    limit: int

    @classmethod
    def from_domain(cls, page: RunItemsPage) -> RunItemsOut:
        return cls(
            run=RunSummaryOut.from_domain(page.run),
            items=[RunItemOut.from_domain(i) for i in page.items],
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        )


class ScoreRunOut(BaseModel):
    run_id: str
    file: str
    md5: str
    label: str | None = None


class PairOut(BaseModel):
    base_run: str
    var_run: str


class ScoreSummaryOut(BaseModel):
    score_id: str
    created: str
    set_name: str
    set_md5: str
    runs: list[ScoreRunOut]
    pairs: list[PairOut]
    records: int
    owner: str | None = None
    state: JobState | None = None

    @classmethod
    def from_domain(cls, s: ScoreSummary) -> ScoreSummaryOut:
        return cls(
            score_id=s.score_id,
            created=s.created,
            set_name=s.set_name,
            set_md5=s.set_md5,
            runs=[ScoreRunOut.model_validate(r) for r in s.runs],
            pairs=[PairOut(base_run=a, var_run=b) for a, b in s.pairs],
            records=s.records,
            owner=s.owner,
            state=JobState(s.state) if s.state else None,
        )


class ScoreGroupOut(BaseModel):
    run_id: str
    group: str = Field(description="`all`, or `field=value` (for example `lang=es`).")
    metric: str
    n: int
    mean: float | None
    ci: tuple[float, float] | None = Field(default=None, description="95% interval.")


class ScoreCompareOut(BaseModel):
    base_run: str
    var_run: str
    group: str
    metric: str
    n: int
    base: float
    var: float
    diff: float = Field(description="Variant minus base.")
    ci: tuple[float, float]
    up: int = Field(description="Questions where the variant scored higher.")
    down: int
    floor: float | None = Field(default=None, description="The noise floor, when a pair was given.")
    counts: bool | None = Field(
        default=None, description="Whether the difference is outside the interval and the floor."
    )


class ScoreOut(BaseModel):
    summary: ScoreSummaryOut
    header: dict[str, Any] = Field(description="The `qa-score/1` header, as written.")
    groups: list[ScoreGroupOut]
    compares: list[ScoreCompareOut]

    @classmethod
    def from_domain(cls, view: ScoreView) -> ScoreOut:
        return cls(
            summary=ScoreSummaryOut.from_domain(view.summary),
            header=view.header,
            groups=[ScoreGroupOut.model_validate(g) for g in view.groups],
            compares=[ScoreCompareOut.model_validate(c) for c in view.compares],
        )


class ScoreItemOut(BaseModel):
    run_id: str
    id: str
    repeat: int
    metrics: dict[str, float | bool | None]
    keys: list[dict[str, Any]] | None = None
    grades: list[dict[str, Any]] | None = None


class ScoreItemsOut(BaseModel):
    items: list[ScoreItemOut]
    total: int
    offset: int
    limit: int

    @classmethod
    def from_domain(cls, page: ScoreItemsPage) -> ScoreItemsOut:
        return cls(
            items=[ScoreItemOut.model_validate(i) for i in page.items],
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        )
