import asyncio
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
