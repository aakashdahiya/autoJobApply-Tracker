"""CLI: build a shape-targeted, ATS-safe resume from the fact bank.

    python -m resume.render --profile profile.yaml --shape ml_platform --out out/resume.pdf

Exits non-zero if any validation or PDF-verification check fails, so this is
safe to wire into CI or a pre-send hook. A resume that cannot be traced back to
the fact bank is never written to its final path.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from resume.docx_render import render_docx
from resume.loader import load_profile
from resume.schema import Shape
from resume.select import depth_report, select
from resume.typst_render import render_pdf
from resume.verify import check_selection, verify_pdf


def _print_depth(report) -> None:
    print("Fact bank depth by shape:")
    for d in report:
        mark = "ok  " if d.ok else "WARN"
        print(f"  [{mark}] {d.shape.value:<16} {d.message}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="resume.render", description=__doc__)
    ap.add_argument("--profile", default="profile.yaml", help="path to the fact bank")
    ap.add_argument(
        "--shape",
        type=Shape,
        choices=list(Shape),
        help="which role shape to target; omit with --depth-only",
    )
    ap.add_argument("--out", help="output PDF path")
    ap.add_argument("--docx", help="also write DOCX to this path")
    ap.add_argument("--depth-only", action="store_true", help="report fact depth and stop")
    ap.add_argument(
        "--allow-rephrased",
        action="store_true",
        help="permit bullets that differ from their source fact (Phase 3 tailoring); "
        "number and skill traceability are still enforced",
    )
    args = ap.parse_args(argv)

    profile = load_profile(args.profile)
    report = depth_report(profile)
    _print_depth(report)

    if args.depth_only:
        return 0
    if not args.shape or not args.out:
        ap.error("--shape and --out are required unless --depth-only is given")

    shape_depth = next(d for d in report if d.shape is args.shape)
    if not shape_depth.ok:
        print(
            f"\nwarning: {args.shape.value} is thin — {shape_depth.message}",
            file=sys.stderr,
        )

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

    pdf = render_pdf(profile, selection, args.out)
    pdf_problems = verify_pdf(pdf, profile, selection)
    if pdf_problems:
        print(f"\nPDF verification failed for {pdf}:", file=sys.stderr)
        for p in pdf_problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    print(f"\nWrote {pdf}")
    print(
        f"  shape={args.shape.value}  bullets={len(selection.bullets)}"
        f"  roles={len(selection.roles)}"
        f"  skills={sum(len(v) for v in selection.skills.values())}"
    )
    print("  traceability ok, PDF text layer verified")

    if args.docx:
        docx = render_docx(profile, selection, args.docx)
        print(f"Wrote {docx}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
