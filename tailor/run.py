"""CLI: score a job description, and tailor a resume to it if it clears the gate.

    python -m tailor.run --jd posting.txt
    python -m tailor.run --jd posting.txt --tailor --out out/cohere.pdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from resume.docx_render import render_docx
from resume.loader import load_profile
from resume.select import select
from resume.typst_render import render_pdf
from resume.verify import check_selection, verify_pdf
from tailor.jd import analyse
from tailor.rephrase import rephrase
from tailor.score import DEFAULT_THRESHOLD, score


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tailor.run", description=__doc__)
    ap.add_argument("--profile", default="profile.yaml")
    ap.add_argument("--jd", required=True, help="path to the job description text")
    ap.add_argument("--title", default="", help="job title, for the rephrasing prompt")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--tailor", action="store_true", help="render a resume if it passes")
    ap.add_argument("--rephrase", action="store_true", help="use the model to reword bullets")
    ap.add_argument("--out", help="output PDF path")
    ap.add_argument("--docx")
    ap.add_argument("--force", action="store_true", help="tailor even below the threshold")
    args = ap.parse_args(argv)

    profile = load_profile(args.profile)
    text = Path(args.jd).read_text(encoding="utf-8")
    jd = analyse(text)
    result = score(profile, jd, threshold=args.threshold)

    print(f"Shape     : {result.shape.value} (confidence {jd.shape_confidence:.0%})")
    print(f"Score     : {result.total:.0f}/100  {'PASS' if result.passes else 'SKIP'}")
    print(f"Reason    : {result.reason}")
    if result.years_required:
        print(f"Experience: posting wants {result.years_required}y, profile shows "
              f"{result.years_have:.1f}y (penalty {result.years_penalty:.0f})")
    print(f"Matched   : {', '.join(result.required_matched) or '(none)'}")
    if result.gaps:
        print(f"Gaps      : {', '.join(result.gaps)}")
        print("            (a skip signal and a learning list — never a claim to make)")

    if not args.tailor:
        return 0
    if not result.passes and not args.force:
        print("\nNot tailoring: below the threshold. Use --force to override.")
        return 0
    if not args.out:
        ap.error("--out is required with --tailor")

    selection = select(profile, result.shape)
    if args.rephrase:
        rewritten = rephrase(
            profile, selection,
            target_terms=sorted(jd.required | jd.preferred),
            title=args.title,
        )
        if rewritten.used_model:
            print(f"\nRephrased {rewritten.changed} of {len(selection.bullets)} bullets.")
        else:
            print(f"\nKept the original wording: {rewritten.note}")
        selection = rewritten.selection

    problems = check_selection(profile, selection, strict=not args.rephrase)
    if problems:
        print("\nTraceability check failed:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    pdf = render_pdf(profile, selection, args.out)
    pdf_problems = verify_pdf(pdf, profile, selection)
    if pdf_problems:
        print(f"\nPDF verification failed: {pdf_problems[0]}", file=sys.stderr)
        return 1
    print(f"\nWrote {pdf}")
    if args.docx:
        print(f"Wrote {render_docx(profile, selection, args.docx)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
