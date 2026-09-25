"""CLI: sweep the inbox, age out silence, print the digest.

    python -m inbox.run --digest
    python -m inbox.run --sync --ghost --digest
"""

from __future__ import annotations

import argparse
import sys

from api.db import make_engine, make_session_factory
from inbox import digest as digest_module
from inbox.sync import GmailSource, build_gmail_service, ghost_stale, sync


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="inbox.run", description=__doc__)
    ap.add_argument("--sync", action="store_true", help="pull new mail from Gmail")
    ap.add_argument("--ghost", action="store_true", help="age silent applications out")
    ap.add_argument("--digest", action="store_true", help="print the digest")
    ap.add_argument("--ghost-days", type=int, default=21)
    ap.add_argument("--token", default="data/gmail_token.json")
    ap.add_argument("--credentials", default="credentials.json")
    args = ap.parse_args(argv)

    if not (args.sync or args.ghost or args.digest):
        ap.error("nothing to do — pass --sync, --ghost or --digest")

    session = make_session_factory(make_engine())()
    try:
        if args.sync:
            try:
                service = build_gmail_service(args.token, args.credentials)
            except Exception as error:
                print(f"Gmail unavailable: {error}", file=sys.stderr)
                print("Run once interactively to complete the OAuth consent.", file=sys.stderr)
                return 1
            report = sync(session, GmailSource(service))
            print(
                f"Synced {report.seen} messages: {report.linked} linked, "
                f"{report.orphans} unplaced, {report.duplicates} already seen."
            )
            for moved in report.moved:
                print(f"  moved: {moved}")
            for urgent in report.urgent:
                print(f"  URGENT {urgent}")

        if args.ghost:
            ghosted = ghost_stale(session, days=args.ghost_days)
            print(f"Aged out {len(ghosted)} silent application(s) after {args.ghost_days} days.")

        if args.digest:
            print()
            print(digest_module.render(digest_module.build(session)))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
