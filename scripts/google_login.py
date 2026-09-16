"""One-time Google login that produces a token for the workflows, without a service account key.

Use this when your Google Cloud organisation blocks service account key creation
(policy iam.disableServiceAccountKeyCreation). Steps in README, section "Google Sheet access".

  python scripts/google_login.py path/to/client_secret.json

Opens a browser, you sign in as the Google account that owns the sheet, and the script prints a
JSON blob. Save that blob as the GitHub secret GOOGLE_OAUTH_TOKEN_JSON.
"""
import json
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

if __name__ == "__main__":
    if len(sys.argv) != 2 or not Path(sys.argv[1]).exists():
        print(__doc__)
        sys.exit(1)
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    token = json.loads(creds.to_json())
    if not token.get("refresh_token"):
        print("No refresh token returned. Remove the app from https://myaccount.google.com/permissions and run again.")
        sys.exit(1)
    print()
    print("Copy everything between the lines into the GitHub secret GOOGLE_OAUTH_TOKEN_JSON:")
    print("-" * 70)
    print(json.dumps({k: token[k] for k in ("client_id", "client_secret", "refresh_token", "token_uri", "scopes") if k in token}))
    print("-" * 70)
