"""Google Sheets access, kept to the two calls the mirror needs.

Separate OAuth token from Gmail's: the mirror needs write access to one
spreadsheet, and the inbox sweep needs read-only mail. Keeping the scopes in
different tokens means neither grant is wider than its job.
"""

from __future__ import annotations

from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class GoogleSheetIO:
    """Reads and writes one spreadsheet through the official client."""

    def __init__(self, service, spreadsheet_id: str):
        self.service = service
        self.spreadsheet_id = spreadsheet_id

    def read(self, a1: str) -> list[list[str]]:
        response = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=a1)
            .execute()
        )
        return response.get("values", [])

    def write(self, a1: str, values: list[list[str]]) -> None:
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=a1,
            valueInputOption="RAW",
            body={"values": values},
        ).execute()

        # Rows left over from a longer previous push would otherwise linger as
        # ghost applications. Clearing after the write, never before, means a
        # failed update leaves the old sheet intact.
        tab = a1.split("!")[0]
        self.service.spreadsheets().values().clear(
            spreadsheetId=self.spreadsheet_id,
            range=f"{tab}!A{len(values) + 1}:Z10000",
            body={},
        ).execute()

    def ensure_tab(self, title: str) -> None:
        meta = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        names = {s["properties"]["title"] for s in meta.get("sheets", [])}
        if title in names:
            return
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
        ).execute()


def build_sheets_service(token_path: str = "data/sheets_token.json",
                         credentials_path: str = "credentials.json"):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token = Path(token_path)
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text(creds.to_json())
    return build("sheets", "v4", credentials=creds)
