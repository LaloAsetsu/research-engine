"""Optional asynchronous ``rag-anything`` document converter.

The adapter intentionally depends only on the parser facade exposed by rag-anything. It does not
instantiate LightRAG or issue model/network requests; callers receive parser output and decide
how it is persisted and indexed.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ...config import RagAnythingConfig
from ...domain.errors import ConversionError
from ...domain.models import PageMethod
from ...ports import Conversion, Segment


def _version() -> str:
    try:
        return version("raganything")
    except PackageNotFoundError:
        return "missing"


def _text_from_block(block: object) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""
    for key in ("text", "content", "markdown", "latex"):
        value = block.get(key)
        if isinstance(value, str):
            return value
    return ""


def _artifacts(blocks: list[object]) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    for index, block in enumerate(blocks, start=1):
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or block.get("category") or "region")
        artifacts.append(
            {
                "id": f"rag-{kind}-{index}",
                "kind": kind,
                "page": block.get("page_idx", block.get("page")),
                "source": "rag-anything",
                "confidence": block.get("confidence"),
                "metadata": {
                    key: str(block[key])
                    for key in ("img_path", "table_body", "equation")
                    if key in block
                },
            }
        )
    return artifacts


class RagAnythingConverter:
    """Run rag-anything's parser off the event loop and expose structured provenance."""

    def __init__(self, config: RagAnythingConfig):
        self.config = config
        self.fingerprint = (
            f"rag-anything {_version()} parser={config.parser} method={config.method} "
            f"tier={config.tier} ocr={config.ocr_mode} lang={config.lang or '-'} "
            f"raw={config.raw_output} layout={config.require_layout}"
        )

    async def convert_async(self, path: Path) -> Conversion:
        return await asyncio.to_thread(self._convert, path)

    def convert(self, path: Path) -> Conversion:
        raise ConversionError(
            "The rag-anything adapter is asynchronous; use IngestService.ingest_async() "
            "or select a synchronous converter."
        )

    def _convert(self, path: Path) -> Conversion:
        if self.config.parser == "mineru" and self._mineru4_installed():
            return self._convert_mineru4(path)

        try:
            from raganything.parser import get_parser  # type: ignore[reportMissingImports]
        except ImportError as error:
            raise ConversionError(
                "The rag-anything adapter needs the optional dependency: "
                "uv sync --extra rag-anything"
            ) from error

        try:
            parser = get_parser(self.config.parser)
            blocks = parser.parse_document(
                path,
                method=self.config.method,
                output_dir=self.config.output_dir,
                lang=self.config.lang,
            )
        except Exception as error:
            raise ConversionError(
                f"rag-anything parser {self.config.parser!r} failed on {path.name}: {error}"
            ) from error
        if not isinstance(blocks, list):
            raise ConversionError(
                f"rag-anything parser returned {type(blocks).__name__}, expected a list of blocks"
            )
        if self.config.raw_output:
            try:
                raw_size = len(json.dumps(blocks, ensure_ascii=False).encode())
            except (TypeError, ValueError) as error:
                raise ConversionError(
                    f"rag-anything returned non-serializable raw output for {path.name}: {error}"
                ) from error
            if raw_size > self.config.max_raw_bytes:
                raise ConversionError(
                    f"rag-anything raw output for {path.name} is {raw_size} bytes, above "
                    f"[rag_anything] max_raw_bytes={self.config.max_raw_bytes}"
                )
        text = "\n\n".join(
            value for value in (_text_from_block(block) for block in blocks) if value
        )
        if not text.strip():
            raise ConversionError(f"rag-anything produced no textual content for {path.name}")
        return Conversion(
            segments=[Segment(text)],
            method=PageMethod.RAG_ANYTHING,
            engine=self.fingerprint,
            artifacts=_artifacts(blocks),
            raw_output=blocks if self.config.raw_output else None,
        )

    @staticmethod
    def _mineru4_installed() -> bool:
        try:
            return version("mineru").split(".", 1)[0] == "4"
        except PackageNotFoundError:
            return False

    def _convert_mineru4(self, path: Path) -> Conversion:
        """Use MinerU 4's local CLI instead of rag-anything's legacy command."""
        output_dir = Path(self.config.output_dir).expanduser().resolve()
        document_dir = output_dir / path.stem
        document_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = document_dir / f"{path.stem}.md"
        ocr_mode = "txt" if self.config.method == "txt" else self.config.ocr_mode
        command = [
            "mineru-kit",
            "parse",
            str(path),
            "-o",
            str(markdown_path),
            "--tier",
            self.config.tier,
            "--ocr-mode",
            ocr_mode,
        ]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as error:
            raise ConversionError(
                "MinerU 4 is not available; install it with "
                "`uv sync --locked --extra rag-anything` and verify `uv run mineru-kit --help`."
            ) from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise ConversionError(
                f"MinerU 4 failed on {path.name} with exit code {result.returncode}: {detail}"
            )
        try:
            text = markdown_path.read_text(encoding="utf-8")
        except OSError as error:
            raise ConversionError(
                f"MinerU 4 completed without readable Markdown output for {path.name}: {error}"
            ) from error
        if not text.strip():
            raise ConversionError(f"MinerU 4 produced no textual content for {path.name}")
        layout = self._read_layout_output(document_dir, path)
        if self.config.require_layout and (
            not isinstance(layout, dict) or not isinstance(layout.get("pages"), list)
        ):
            raise ConversionError(
                f"MinerU 4 produced no usable structured layout JSON for {path.name}; "
                "disable [rag_anything] require_layout only when text-only output is intentional"
            )
        artifacts = _artifacts(self._layout_blocks(layout)) if layout is not None else []
        raw_output = None
        if self.config.raw_output:
            raw_output = {
                "backend": "mineru-kit",
                "tier": self.config.tier,
                "ocr_mode": ocr_mode,
                "markdown": text,
                "layout": layout,
            }
            try:
                raw_size = len(json.dumps(raw_output, ensure_ascii=False).encode())
            except (TypeError, ValueError) as error:
                raise ConversionError(
                    f"MinerU 4 returned non-serializable output for {path.name}: {error}"
                ) from error
            if raw_size > self.config.max_raw_bytes:
                raise ConversionError(
                    f"MinerU 4 raw output for {path.name} is {raw_size} bytes, above "
                    f"[rag_anything] max_raw_bytes={self.config.max_raw_bytes}"
                )
        return Conversion(
            segments=[Segment(text)],
            method=PageMethod.RAG_ANYTHING,
            engine=self.fingerprint,
            artifacts=artifacts,
            raw_output=raw_output,
        )

    def _read_layout_output(self, document_dir: Path, path: Path) -> object | None:
        candidates = (
            "middle_json.json",
            "structured_content.json",
            "model_output.json",
            f"{path.stem}.json",
        )
        paths = {candidate: document_dir / candidate for candidate in candidates}
        discovered = next(
            (candidate for candidate in candidates if paths[candidate].is_file()),
            None,
        )
        if discovered is None:
            json_files = sorted(document_dir.rglob("*.json"))
            if len(json_files) == 1:
                layout_path = json_files[0]
            else:
                return None
        else:
            layout_path = paths[discovered]
        try:
            return json.loads(layout_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ConversionError(
                f"MinerU 4 produced unreadable structured layout output for {path.name}: {error}"
            ) from error

    @staticmethod
    def _layout_blocks(layout: object) -> list[object]:
        if not isinstance(layout, dict):
            return []
        pages = layout.get("pages")
        if not isinstance(pages, list):
            return []
        blocks: list[object] = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_index = page.get("page_idx", page.get("page"))
            page_blocks = page.get("blocks")
            if not isinstance(page_blocks, list):
                continue
            for block in page_blocks:
                if isinstance(block, dict):
                    blocks.append({**block, "page_idx": page_index})
        return blocks
