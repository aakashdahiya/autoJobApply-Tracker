"""CLI: mirror the tracker into a Google Sheet, and pull your edits back.

    python -m sheets.run --spreadsheet 1AbC...xyz

The sheet id is the long string in its URL:
https://docs.google.com/spreadsheets/d/<THIS PART>/edit
"""

from __future__ import annotations

import argparse
import os
import sys

from api.db import make_engine, make_session_factory
from sheets import mirror


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sheets.run", description=__doc__)
    ap.add_argument("--spreadsheet", default=os.getenv("TRACKER_SPREADSHEET_ID"),
                    help="spreadsheet id (or set TRACKER_SPREADSHEET_ID)")
    ap.add_argument("--tab", default=mirror.SHEET_NAME)
    ap.add_argument("--token", default="data/sheets_token.json")
    ap.add_argument("--credentials", default="credentials.json")
    ap.add_argument("--push-only", action="store_true",
                    help="overwrite the sheet without reading your edits first")
    args = ap.parse_args(argv)

    if not args.spreadsheet:
        ap.error("--spreadsheet is required (or set TRACKER_SPREADSHEET_ID)")

    try:
        from sheets.client import GoogleSheetIO, build_sheets_service

        io = GoogleSheetIO(build_sheets_service(args.token, args.credentials), args.spreadsheet)
        io.ensure_tab(args.tab)
    except Exception as error:
        print(f"Sheets unavailable: {error}", file=sys.stderr)
        print("Run once interactively to complete the OAuth consent.", file=sys.stderr)
        return 1

    session = make_session_factory(make_engine())()
    try:
        if args.push_only:
            written = mirror.push(session, io, sheet=args.tab)
            print(f"{written} rows written.")
            return 0
        report = mirror.sync(session, io, sheet=args.tab)
    finally:
        session.close()

    print(report.summary())
    for change in report.status_changes:
        print(f"  status: {change}")
    for problem in report.rejected:
        print(f"  ! {problem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
