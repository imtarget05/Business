"""Google API client utilities (Task 5.2).

Provides:
- get_google_credentials(): builds Credentials from GOOGLE_REFRESH_TOKEN +
  client id/secret (google.oauth2.credentials.Credentials), auto-refreshes via
  google.auth.transport.requests.Request
- gmail_send(to, subject, body): Gmail API users.messages.send (base64url raw)
- sheet_log_row(values: list[str]): Sheets API values.append to range
  'Trang tính1'!A:E
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from packages.config.settings import get_settings


def get_google_credentials() -> Credentials:
    """Build and return Google OAuth2 credentials with auto-refresh.

    Uses environment variables:
    - GOOGLE_REFRESH_TOKEN: OAuth2 refresh token
    - GOOGLE_OAUTH_CLIENT_ID: OAuth2 client ID
    - GOOGLE_OAUTH_CLIENT_SECRET: OAuth2 client secret

    Returns:
        Credentials object that will auto-refresh access tokens on API calls.

    Raises:
        ValueError: If required environment variables are not set.
    """
    settings = get_settings()

    refresh_token = settings.google_refresh_token
    client_id = settings.google_oauth_client_id
    client_secret = settings.google_oauth_client_secret

    if not refresh_token:
        raise ValueError("google_refresh_token not configured")
    if not client_id:
        raise ValueError("google_oauth_client_id not configured")
    if not client_secret:
        raise ValueError("google_oauth_client_secret not configured")

    credentials = Credentials(
        token=None,  # Will be auto-refreshed on first use
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=[
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/spreadsheets",
        ],
    )

    # Force an initial refresh to validate credentials and get a valid access token
    credentials.refresh(Request())

    return credentials


def _get_gmail_service():
    """Get authenticated Gmail API service."""
    credentials = get_google_credentials()
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def _get_sheets_service():
    """Get authenticated Sheets API service."""
    credentials = get_google_credentials()
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def gmail_send(to: str, subject: str, body: str) -> dict[str, Any]:
    """Send an email via Gmail API.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Email body text (plain text).

    Returns:
        Dictionary with the Gmail API response (contains 'id' of sent message).

    Raises:
        Exception: Propagates any Gmail API errors.
    """
    service = _get_gmail_service()

    # Create the email message
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    # Encode as base64url (RFC 4648 §5) for Gmail API raw format
    raw_bytes = message.as_bytes()
    raw_base64url = base64.urlsafe_b64encode(raw_bytes).decode("ascii")

    # Send via Gmail API
    send_request = service.users().messages().send(userId="me", body={"raw": raw_base64url})
    response = send_request.execute()

    return response


def sheet_log_row(values: list[str]) -> dict[str, Any]:
    """Append a row to the Google Sheet.

    Args:
        values: List of cell values to append (will be written to columns A:E).

    Returns:
        Dictionary with the Sheets API response (contains 'updates' info).

    Raises:
        Exception: Propagates any Sheets API errors.
    """
    settings = get_settings()
    sheet_id = settings.google_sheet_id

    if not sheet_id:
        raise ValueError("google_sheet_id not configured")

    service = _get_sheets_service()

    # Append row to 'Trang tính1'!A:E
    range_name = "'Trang tính1'!A:E"
    body = {"values": [values]}

    append_request = (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=sheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        )
    )
    response = append_request.execute()

    return response
