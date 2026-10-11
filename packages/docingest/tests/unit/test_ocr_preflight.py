"""A missing OCR backend stops the run once, with the way out, instead of failing file by file."""

import sys

import pytest
from fakes import CheckedOcr, FakeCrawler, FakeOcr, blank, digital, make_service, record, scanned
from typer.testing import CliRunner

from docingest.adapters.ocr.mlx_vlm import MlxVlmOcr
from docingest.adapters.ocr.profiles import profile_for
from docingest.application.crawl import CrawlService
from docingest.domain.errors import OcrUnavailableError
from docingest.domain.models import ModelRef, PageMethod
from docingest.entrypoints.cli import app
from docingest.ports import OcrPreflight

TEXT = "Attention is all you need. " * 10
BROKEN = OcrUnavailableError("the OCR backend is not installed")


def test_the_adapters_with_a_check_satisfy_the_preflight_port():
    assert isinstance(CheckedOcr(), OcrPreflight)
    assert not isinstance(FakeOcr(), OcrPreflight)  # engines without it work as before


# ------------------------------------------------------------------- the mlx adapter
def mlx_engine() -> MlxVlmOcr:
    return MlxVlmOcr(ModelRef(repo_id="org/m", revision="0" * 40), profile_for("markdown"))


def test_a_missing_mlx_vlm_names_the_setting_and_both_ways_out(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlx_vlm", None)  # what a failed import leaves behind
    with pytest.raises(OcrUnavailableError) as err:
        mlx_engine().check()
    message = str(err.value)
    assert '[adapters] ocr = "mlx-vlm"' in message and "mlx_vlm" in message
    assert "mlx extra" in message and "-c" in message and "openai-compatible" in message


def test_an_importable_mlx_vlm_passes_and_loads_no_model(monkeypatch):
    import types

    monkeypatch.setitem(sys.modules, "mlx_vlm", types.ModuleType("mlx_vlm"))
    engine = mlx_engine()
    engine.check()
    assert engine._model is None  # check() imports; it does not load weights


# ------------------------------------------------------------------- the service
def test_the_check_runs_once_before_the_first_page_that_needs_ocr(src):
    ocr = CheckedOcr()
    svc = make_service({"a.pdf": [digital(TEXT), scanned(), scanned()]}, ocr=ocr)
    svc.ingest(src("a.pdf"))
    assert ocr.checks == 1 and ocr.calls == 2
    svc.ingest(src("a.pdf", b"another"))  # a second document: still one check
    assert ocr.checks == 1


def test_text_only_documents_never_check_the_backend(src):
    ocr = CheckedOcr(BROKEN)
    svc = make_service({"a.pdf": [digital(TEXT)] * 3}, ocr=ocr)
    doc = svc.ingest(src("a.pdf"))
    assert doc.manifest.ocr_pages == 0 and ocr.checks == 0


def test_blank_pages_never_need_the_backend(src):
    ocr = CheckedOcr(BROKEN)
    svc = make_service({"a.pdf": [blank(), digital(TEXT), blank()]}, ocr=ocr)
    doc = svc.ingest(src("a.pdf"))
    assert doc.manifest.ocr_pages == 0 and ocr.checks == 0


def test_a_failed_check_stops_before_any_page_is_transcribed_or_stored(src):
    ocr = CheckedOcr(BROKEN)
    svc = make_service({"a.pdf": [scanned(), scanned()]}, ocr=ocr)
    with pytest.raises(OcrUnavailableError, match="not installed"):
        svc.ingest(src("a.pdf"))
    assert ocr.calls == 0 and not svc.store.canonical


def test_a_cached_result_needs_no_backend(src):
    ocr = CheckedOcr()
    svc = make_service({"a.pdf": [scanned()]}, ocr=ocr)
    path = src("a.pdf")
    svc.ingest(path)
    ocr.error = BROKEN  # the backend goes away; the result is already stored
    assert svc.ingest(path).manifest.pages[0].method == PageMethod.VLM_OCR
    assert ocr.checks == 1


def test_images_check_the_backend_too(src):
    ocr = CheckedOcr(BROKEN)
    svc = make_service(ocr=ocr)
    with pytest.raises(OcrUnavailableError):
        svc.ingest(src("scan.png", b"png"))


def test_the_check_is_repeated_after_it_failed(src):
    ocr = CheckedOcr(BROKEN)
    svc = make_service({"a.pdf": [scanned()]}, ocr=ocr)
    for _ in range(2):
        with pytest.raises(OcrUnavailableError):
            svc.ingest(src("a.pdf"))
    ocr.error = None  # the server was started
    assert svc.ingest(src("a.pdf")).manifest.ocr_pages == 1


# ------------------------------------------------------------------- the commands
def write_config(tmp_path, ocr: str, extra: str = "") -> str:
    path = tmp_path / "cfg.toml"
    path.write_text(f'output_dir = "{tmp_path / "out"}"\n[adapters]\nocr = "{ocr}"\n{extra}')
    return str(path)


def test_ingest_stops_with_one_message_instead_of_failing_every_scan(tmp_path, monkeypatch):
    from builders import LONG, image_pdf, text_pdf

    monkeypatch.setitem(sys.modules, "mlx_vlm", None)
    raw = tmp_path / "raw"
    raw.mkdir()
    text_pdf(raw / "a_digital.pdf", LONG)
    image_pdf(raw / "b_scan.pdf")
    (raw / "b_scan.pdf").with_name("c_scan.pdf").write_bytes(
        (raw / "b_scan.pdf").read_bytes() + b"\n"
    )
    cfg = write_config(tmp_path, "mlx-vlm")
    result = CliRunner().invoke(app, ["ingest", str(raw), "-c", cfg], env={"COLUMNS": "400"})
    out = result.output
    assert result.exit_code == 1
    assert out.count("mlx extra") == 1  # said once, not once per scanned file
    assert "stopped at b_scan.pdf" in out and "c_scan.pdf" not in out.split("stopped at", 1)[1]
    assert "Failed inputs" not in out
    assert "a_digital.pdf" in out  # the text-layer paper before it was ingested
    assert (tmp_path / "out" / "index.json").exists()


def test_a_run_without_scans_does_not_need_the_default_backend(tmp_path, monkeypatch):
    from builders import LONG, text_pdf

    monkeypatch.setitem(sys.modules, "mlx_vlm", None)
    raw = tmp_path / "raw"
    raw.mkdir()
    text_pdf(raw / "a.pdf", LONG)
    result = CliRunner().invoke(app, ["ingest", str(raw), "-c", write_config(tmp_path, "mlx-vlm")])
    assert result.exit_code == 0, result.output


def test_the_crawl_stops_at_the_first_record_that_needs_the_backend(tmp_path):
    papers = [record("2401.00001"), record("2401.00002"), record("2401.00003")]
    seen = []

    class Ingest:
        def ingest(self, path, options, metadata):
            seen.append(path.name)
            raise BROKEN

    service = CrawlService(
        crawler=FakeCrawler(papers),
        ingest=Ingest,  # type: ignore[arg-type]
        raw_dir=tmp_path,
        log=lambda _: None,
    )
    report = service.run("q", 3)
    assert seen == ["2401.00001.tex"]  # the others were not even downloaded
    assert report.stopped and "OCR engine cannot be used" in report.stopped
    assert [k for k, _ in report.failures] == ["2401.00001", "2401.00002", "2401.00003"]
    assert all("not attempted" in why for _, why in report.failures[1:])
