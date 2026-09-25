"""CLI: build a shape-targeted, ATS-safe resume from the fact bank.

    python -m resume.render --profile profile.yaml --shape ai_engineer --out out/resume.pdf

Exits non-zero if any traceability or PDF-verification check fails, so a resume
that cannot be traced back to the fact bank is never handed over.
"""

from __future__ import annotations

import argparse
import sys

from resume.docx_render import render_docx
from resume.loader import load_profile
from resume.schema import Shape
from resume.select import depth_report, select, skills_without_evidence
from resume.typst_render import render_pdf
from resume.verify import check_selection, reading_order_risks, verify_pdf


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="resume.render", description=__doc__)
    ap.add_argument("--profile", default="profile.yaml")
    ap.add_argument("--shape", type=Shape, choices=list(Shape))
    ap.add_argument("--out", help="output PDF path")
    ap.add_argument("--docx", help="also write DOCX here")
    ap.add_argument("--depth-only", action="store_true", help="report fact depth and stop")
    ap.add_argument(
        "--date-style",
        choices=["tab", "inline"],
        default="tab",
        help="tab: dates at the right margin, matching the source resume. "
        "inline: appended after the company, which no extractor can misread.",
    )
    ap.add_argument("--no-work-auth-line", action="store_true")
    ap.add_argument(
        "--allow-rephrased",
        action="store_true",
        help="permit bullets that differ from their source fact (Phase 3 tailoring); "
        "number and attribution traceability are still enforced",
    )
    args = ap.parse_args(argv)

    profile = load_profile(args.profile)
    report = depth_report(profile)
    print("Fact bank depth by shape:")
    for d in report:
        print(f"  [{'ok  ' if d.ok else 'WARN'}] {d.shape.value:<16} {d.message}")

    if args.depth_only:
        return 0
    if not args.shape or not args.out:
        ap.error("--shape and --out are required unless --depth-only is given")

    shape_depth = next(d for d in report if d.shape is args.shape)
    if not shape_depth.ok:
        print(f"\nwarning: {args.shape.value} is {shape_depth.message}", file=sys.stderr)

    selection = select(profile, args.shape)
    if not selection.bullets:
        print(f"error: no facts tagged for shape {args.shape.value}", file=sys.stderr)
        return 1

    problems = check_selection(profile, selection, strict=not args.allow_rephrased)
    if problems:
        print("\nTraceability check failed:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    pdf = render_pdf(
        profile,
        selection,
        args.out,
        date_style=args.date_style,
        work_auth_line=not args.no_work_auth_line,
    )
    pdf_problems = verify_pdf(pdf, profile, selection)
    if pdf_problems:
        print(f"\nPDF verification failed for {pdf}:", file=sys.stderr)
        for p in pdf_problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    print(f"\nWrote {pdf}")
    print(
        f"  shape={args.shape.value}  bullets={len(selection.bullets)}"
        f"  entries={len(selection.entries)}  date-style={args.date_style}"
    )
    print("  traceability ok, PDF text layer verified")

    risks = reading_order_risks(pdf, selection)
    if risks:
        print(
            f"  advisory: {len(risks)} bullet(s) a naive parser would read as interrupted "
            f"by the right-aligned date or tech line — rerun with --date-style inline "
            f"to remove the risk",
            file=sys.stderr,
        )

    unevidenced = skills_without_evidence(profile, selection)
    if unevidenced:
        print(f"  advisory: skills with no bullet on this page: {', '.join(unevidenced)}")

    if args.docx:
        docx = render_docx(
            profile,
            selection,
            args.docx,
            date_style=args.date_style,
            work_auth_line=not args.no_work_auth_line,
        )
        print(f"Wrote {docx}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
