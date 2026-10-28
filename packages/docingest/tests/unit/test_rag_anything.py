import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

from docingest.adapters.converters.rag_anything import RagAnythingConverter
from docingest.config import RagAnythingConfig
from docingest.domain.errors import ConversionError


def _install_parser(monkeypatch, blocks):
    parser_module = types.ModuleType("raganything.parser")

    class Parser:
        def parse_document(self, path, **kwargs):
            assert Path(path).name == "paper.pdf"
            assert kwargs["method"] == "auto"
            return blocks

    parser_module.get_parser = lambda name: Parser()
    package = types.ModuleType("raganything")
    package.parser = parser_module
    monkeypatch.setitem(sys.modules, "raganything", package)
    monkeypatch.setitem(sys.modules, "raganything.parser", parser_module)


def test_async_converter_normalizes_blocks_and_keeps_raw_output(monkeypatch, tmp_path):
    blocks = [
        {"type": "text", "text": "A paper section", "page_idx": 2},
        {"type": "equation", "latex": r"E=mc^2", "confidence": 0.9},
    ]
    _install_parser(monkeypatch, blocks)
    converter = RagAnythingConverter(
        RagAnythingConfig(output_dir=str(tmp_path), raw_output=True)
    )

    result = asyncio.run(converter.convert_async(tmp_path / "paper.pdf"))

    assert result.segments[0].text == "A paper section\n\nE=mc^2"
    assert result.artifacts[0]["kind"] == "text"
    assert result.raw_output == blocks


def test_async_converter_rejects_empty_parser_output(monkeypatch, tmp_path):
    _install_parser(monkeypatch, [])
    converter = RagAnythingConverter(RagAnythingConfig(output_dir=str(tmp_path)))

    with pytest.raises(ConversionError, match="no textual content"):
        asyncio.run(converter.convert_async(tmp_path / "paper.pdf"))


def test_mineru4_preserves_middle_json_layout(monkeypatch, tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    output = tmp_path / "output"
    document_dir = output / source.stem
    document_dir.mkdir(parents=True)
    (document_dir / "paper.md").write_text("# Paper\n\nText", encoding="utf-8")
    (document_dir / "middle_json.json").write_text(
        json.dumps(
            {
                "schema": "docvortex.middle",
                "pages": [
                    {
                        "page_idx": 0,
                        "blocks": [
                            {"type": "equation", "latex": "x^2", "confidence": 0.8}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        RagAnythingConverter,
        "_mineru4_installed",
        staticmethod(lambda: True),
    )
    commands = []

    def run(*args, **kwargs):
        commands.append(args[0])
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "docingest.adapters.converters.rag_anything.subprocess.run",
        run,
    )
    converter = RagAnythingConverter(
        RagAnythingConfig(
            output_dir=str(output),
            raw_output=True,
            require_layout=True,
            method="ocr",
        )
    )

    result = asyncio.run(converter.convert_async(source))

    assert result.artifacts[0]["kind"] == "equation"
    assert result.artifacts[0]["page"] == 0
    assert result.raw_output["layout"]["schema"] == "docvortex.middle"
    assert commands[0][-4:] == ["--tier", "standard", "--ocr-mode", "ocr"]


def test_mineru4_requires_structured_layout_when_configured(monkeypatch, tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    document_dir = tmp_path / "output" / source.stem
    document_dir.mkdir(parents=True)
    (document_dir / "paper.md").write_text("Text", encoding="utf-8")
    monkeypatch.setattr(
        RagAnythingConverter,
        "_mineru4_installed",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(
        "docingest.adapters.converters.rag_anything.subprocess.run",
        lambda *args, **kwargs: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    converter = RagAnythingConverter(
        RagAnythingConfig(
            output_dir=str(tmp_path / "output"),
            require_layout=True,
            method="ocr",
        )
    )

    with pytest.raises(ConversionError, match="no usable structured layout JSON"):
        asyncio.run(converter.convert_async(source))


def test_mineru4_surfaces_subprocess_failure(monkeypatch, tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    monkeypatch.setattr(
        RagAnythingConverter,
        "_mineru4_installed",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(
        "docingest.adapters.converters.rag_anything.subprocess.run",
        lambda *args, **kwargs: types.SimpleNamespace(
            returncode=2,
            stdout="",
            stderr="engine unavailable",
        ),
    )
    converter = RagAnythingConverter(
        RagAnythingConfig(output_dir=str(tmp_path), method="ocr")
    )

    with pytest.raises(ConversionError, match="engine unavailable"):
        asyncio.run(converter.convert_async(source))


@pytest.mark.parametrize(
    ("text", "expected"),
    [("An arXiv text layer", ("flash", "txt")), ("", ("standard", "ocr"))],
)
def test_mineru4_auto_selects_text_or_ocr_mode(monkeypatch, tmp_path, text, expected):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")

    class TextPage:
        def get_text_bounded(self):
            return text

    class Page:
        def get_textpage(self):
            return TextPage()

    class Document:
        def __len__(self):
            return 1

        def __getitem__(self, index):
            return Page()

        def close(self):
            pass

    monkeypatch.setattr(
        "docingest.adapters.converters.rag_anything.pdfium.PdfDocument",
        lambda path: Document(),
    )
    converter = RagAnythingConverter(RagAnythingConfig())

    assert converter._mineru4_mode(source) == expected
