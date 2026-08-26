"""One-off local verification: Google Sheets access via service account.

Reads the sheet metadata and appends one test row to prove write access.
Run: .venv/Scripts/python.exe scripts/verify_google_sheets.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

SHEET_ID = "1VSjKwu2RqvbvzDE4rX7rfdzxXAXObS96vOj3Lie83DI"
KEY_FILE = Path(r"C:\Users\Admin\Downloads\agent-swarm-506705-666c524cebf7.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def main() -> int:
    creds = service_account.Credentials.from_service_account_file(
        str(KEY_FILE), scopes=SCOPES
    )
    svc = build("sheets", "v4", credentials=creds)

    meta = (
        svc.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    )
    print("Title:", meta.get("properties", {}).get("title"))
    for s in meta.get("sheets", []):
        props = s["properties"]
        print(f"  tab: {props['title']} (gid={props['sheetId']}, {props['gridProperties'].get('rowCount')}x{props['gridProperties'].get('columnCount')})")

    tab = meta["sheets"][0]["properties"]["title"]
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    result = (
        svc.spreadsheets()
        .values()
        .append(
            spreadsheetId=SHEET_ID,
            range=f"{tab}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [[now, "verify_google_sheets.py", "OK - service account write test"]]},
        )
        .execute()
    )
    print("Appended:", result.get("updates", {}).get("updatedRange"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)
