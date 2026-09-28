"""CLI: sweep the inbox, age out silence, print the digest.

    python -m inbox.run --digest
    python -m inbox.run --sync --ghost --digest
    python -m inbox.run --notify          # flush anything still owed a push
"""

from __future__ import annotations

import argparse
import sys

from api.db import make_engine, make_session_factory
from inbox import digest as digest_module
from inbox import notify as notify_module
from inbox.sync import GmailSource, build_gmail_service, ghost_stale, sync


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="inbox.run", description=__doc__)
    ap.add_argument("--sync", action="store_true", help="pull new mail from Gmail")
    ap.add_argument("--ghost", action="store_true", help="age silent applications out")
    ap.add_argument("--digest", action="store_true", help="print the digest")
    ap.add_argument("--notify", action="store_true",
                    help="push interviews, assessments and offers that are still owed one")
    ap.add_argument("--no-notify", action="store_true",
                    help="with --sync, triage without sending any push")
    ap.add_argument("--ghost-days", type=int, default=21)
    ap.add_argument("--token", default="data/gmail_token.json")
    ap.add_argument("--credentials", default="credentials.json")
    args = ap.parse_args(argv)

    if not (args.sync or args.ghost or args.digest or args.notify):
        ap.error("nothing to do — pass --sync, --ghost, --notify or --digest")

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

        # A sweep pushes by default. Finding an assessment and saying nothing
        # about it until tomorrow morning is the failure this exists to stop.
        #
        # An undelivered push exits non-zero even when everything else worked,
        # so cron surfaces a broken transport instead of reporting success
        # every night while you hear nothing.
        undelivered = 0
        if (args.sync and not args.no_notify) or args.notify:
            try:
                pushed = notify_module.push_pending(session)
            except notify_module.PushFailed as error:
                undelivered = len(notify_module.pending(session))
                print(f"Notifications not configured: {error}", file=sys.stderr)
            else:
                if pushed.sent or pushed.failed:
                    print(f"Pushed {pushed.sent}, {pushed.failed} still owed.")
                for failure in pushed.errors:
                    print(f"  push failed: {failure}", file=sys.stderr)
                undelivered = pushed.failed
            if undelivered:
                print(f"{undelivered} notification(s) still undelivered.", file=sys.stderr)

        if args.ghost:
            ghosted = ghost_stale(session, days=args.ghost_days)
            print(f"Aged out {len(ghosted)} silent application(s) after {args.ghost_days} days.")

        if args.digest:
            print()
            print(digest_module.render(digest_module.build(session)))
        return 1 if undelivered else 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
