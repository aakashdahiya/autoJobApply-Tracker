"""Rendering, and the extraction check that makes the PDF trustworthy."""

from __future__ import annotations

from docx import Document

from resume.docx_render import render_docx
from resume.render import main
from resume.schema import Shape
from resume.select import select
from resume.typst_render import render_pdf
from resume.verify import normalise, verify_pdf


def test_every_shape_renders_and_survives_extraction(profile, tmp_path):
    """The regression test for the whole ATS-safety claim.

    It once failed for real: right-aligning the employment dates with `#h(1fr)`
    looked correct on screen but made the extractor emit the date in the middle
    of the first bullet, so an ATS read mangled prose. Any layout change that
    reorders the text layer fails here rather than in a recruiter's inbox.
    """
    for shape in Shape:
        selection = select(profile, shape)
        pdf = render_pdf(profile, selection, tmp_path / f"{shape.value}.pdf")
        assert pdf.exists() and pdf.stat().st_size > 0
        assert verify_pdf(pdf, profile, selection) == []


def test_pdf_carries_contact_details_and_work_auth_line(profile, tmp_path):
    selection = select(profile, Shape.ml_platform)
    pdf = render_pdf(profile, selection, tmp_path / "r.pdf")

    from pdfminer.high_level import extract_text

    text = normalise(extract_text(str(pdf)))
    assert normalise(profile.identity.name) in text
    assert normalise(profile.identity.email) in text
    assert normalise("Toronto, ON") in text
    assert normalise("Authorised to work in Canada") in text


def test_pdf_has_no_page_header_or_footer_artifacts(profile, tmp_path):
    """Contact details belong in the body; a repeated header breaks parsers."""
    selection = select(profile, Shape.product_python)
    pdf = render_pdf(profile, selection, tmp_path / "r.pdf")

    from pdfminer.high_level import extract_text

    text = normalise(extract_text(str(pdf)))
    assert text.count(normalise(profile.identity.email)) == 1


def test_docx_contains_every_bullet(profile, tmp_path):
    selection = select(profile, Shape.ml_platform)
    path = render_docx(profile, selection, tmp_path / "r.docx")

    doc = Document(str(path))
    text = normalise(" ".join(p.text for p in doc.paragraphs))
    for bullet in selection.bullets:
        assert normalise(bullet.text) in text
    assert not doc.tables, "tables break ATS parsing"


def test_cli_renders_and_reports(tmp_path, capsys):
    out = tmp_path / "out.pdf"
    code = main(
        [
            "--profile", "profile.example.yaml",
            "--shape", "ml_platform",
            "--out", str(out),
            "--docx", str(tmp_path / "out.docx"),
        ]
    )
    assert code == 0
    assert out.exists()
    captured = capsys.readouterr().out
    assert "traceability ok, PDF text layer verified" in captured


def test_cli_depth_only_needs_no_shape(capsys):
    assert main(["--profile", "profile.example.yaml", "--depth-only"]) == 0
    assert "Fact bank depth by shape" in capsys.readouterr().out


def test_skill_labels_are_display_only(profile, tmp_path):
    """Canonical tokens are what gets verified; labels are only printed.

    Keyword matching wants `ci-cd` and `aws`; a reader wants CI/CD and AWS.
    Splitting the two keeps both honest.
    """
    from resume.typst_render import build_payload

    selection = select(profile, Shape.ml_platform)
    payload = build_payload(profile, selection)
    printed = {s for group in payload["skills"] for s in group["items"]}

    assert "CI/CD" in printed or "AWS" in printed
    assert "ci-cd" not in printed and "aws" not in printed
    # The selection itself still holds canonical tokens, so verification is unaffected.
    canonical = {s for group in selection.skills.values() for s in group}
    assert canonical <= profile.declared_skills
