"""The whole pipeline with real pdfium on a generated multi-page PDF."""

from builders import layout_pdf, paragraph
from fakes import FakeOcr

from docingest.application.ingest import IngestOptions
from docingest.bootstrap import Container
from docingest.domain.models import PageMethod

JOURNAL = "Journal of Synthetic Results"
INTRO = (
    "The transduction models described here were trained on long sequences and the "
    "transduction of unseen inputs worked well in every case we tried, with a loss "
    "that fell steadily. Training took a few hours on a single machine and the final "
    "scores are reported in the tables below."
)
SECOND = (
    "A second paragraph follows after a blank line and discusses the results of the "
    "evaluation in more detail than the first one did, so that it wraps as well."
)


def page(n, *body):
    return [
        (72, 750, 9, False, f"{JOURNAL} {n}"),
        *body,
        (300, 40, 9, False, str(n)),
    ]


def paper(tmp_path):
    first = page(
        1,
        (72, 700, 20, True, "Filters and Noise"),
        (72, 660, 13, True, "1 Introduction"),
        *paragraph(INTRO, top=630),
        *paragraph(SECOND, top=530),
    )
    rest = [
        page(n, *paragraph(INTRO, top=700 - 10 * n), *paragraph(SECOND, top=600)) for n in (2, 3, 4)
    ]
    return layout_pdf(tmp_path / "paper.pdf", [first, *rest])


def container(cfg):
    return Container(cfg, log=lambda _: None, overrides={"ocr": FakeOcr("ocr text")})


def test_ingest_removes_running_lines_joins_paragraphs_and_marks_headings(cfg, tmp_path):
    svc = container(cfg).ingest
    doc = svc.ingest(paper(tmp_path))
    markdown = svc.store.markdown(doc)
    pages = markdown.split("<!-- page ")
    assert JOURNAL not in markdown
    first = pages[1]
    assert "# Filters and Noise" in first and "## 1 Introduction" in first
    assert f"\n\n{INTRO}\n\n{SECOND}" in first  # the wrapped lines are one paragraph each
    assert all(p.method == PageMethod.TEXT_LAYER for p in doc.manifest.pages)
    assert doc.manifest.pages[2].removed_lines == [f"{JOURNAL} 3", "3"]
    assert doc.manifest.pages[0].n_chars == len(first.split("-->", 1)[1].strip())


def test_the_pages_of_a_paper_are_one_paragraph_each_not_one_line_each(cfg, tmp_path):
    svc = container(cfg).ingest
    markdown = svc.store.markdown(svc.ingest(paper(tmp_path)))
    second_page = markdown.split("<!-- page 2 | method=text_layer -->")[1].split("<!-- page 3")[0]
    assert len([p for p in second_page.split("\n\n") if p.strip()]) == 2


def test_a_partial_run_cannot_find_a_header_and_keeps_it(cfg, tmp_path):
    svc = container(cfg).ingest
    doc = svc.ingest(paper(tmp_path), IngestOptions(max_pages=1))
    assert JOURNAL in svc.store.markdown(doc)  # one page: nothing comes back


def test_a_word_split_at_a_line_end_is_rejoined_when_it_occurs_whole_elsewhere(cfg, tmp_path):
    split = [
        (72, 700, 10, False, "the sequence transduc-"),
        (72, 688, 10, False, "tion models work on well-"),
        (72, 676, 10, False, "known tasks for years"),
    ]
    whole = paragraph("the transduction models also appear here in one piece on this page")
    svc = container(cfg).ingest
    doc = svc.ingest(layout_pdf(tmp_path / "split.pdf", [split, whole]))
    first = svc.store.markdown(doc).split("<!-- page 2")[0]
    assert "the sequence transduction models work on well-known tasks for years" in first
