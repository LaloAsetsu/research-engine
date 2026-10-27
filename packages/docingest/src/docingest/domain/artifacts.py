"""Layout artifacts preserved alongside normalized document text.

The records are deliberately parser-neutral. Built-in Markdown parsing can populate them today,
and optional multimodal adapters can add figure/table/region observations without changing the
manifest shape.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from .blocks import find_blocks
from .circuits import circuit_code_blocks


class ArtifactKind(StrEnum):
    EQUATION = "equation"
    TABLE = "table"
    CIRCUIT = "circuit"
    FIGURE = "figure"
    REGION = "region"


class LayoutArtifact(BaseModel):
    id: str
    kind: ArtifactKind
    page: int | None = None
    start: int | None = None
    end: int | None = None
    source: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    review_status: str = "accepted"
    metadata: dict[str, str] = Field(default_factory=dict)


def extract_layout_artifacts(markdown: str) -> list[LayoutArtifact]:
    """Extract deterministic layout records from normalized Markdown.

    Multimodal adapters can add richer records later; these baseline records make layout-aware
    indexing useful even when the optional dependency is not installed.
    """
    circuit_spans = {(block.start, block.end) for block in circuit_code_blocks(markdown)}
    artifacts: list[LayoutArtifact] = []
    for index, block in enumerate(find_blocks(markdown), start=1):
        kind = ArtifactKind.EQUATION if block.kind in {"display", "inline"} else ArtifactKind.TABLE
        if block.kind == "code" and (block.start, block.end) in circuit_spans:
            kind = ArtifactKind.CIRCUIT
        elif block.kind == "code":
            kind = ArtifactKind.REGION
        artifacts.append(
            LayoutArtifact(
                id=f"{kind.value}-{index}",
                kind=kind,
                start=block.start,
                end=block.end,
                source="markdown",
            )
        )
    return artifacts
