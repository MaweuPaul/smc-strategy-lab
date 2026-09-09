"""
One-time interactive OAuth setup for the cTrader Open API.

Reads CTRADER_CLIENT_ID / CTRADER_CLIENT_SECRET / CTRADER_REDIRECT_URI from a
local .env file (never committed), prints the authorization URL for you to
open and approve in a browser, catches the redirect locally to grab the
auth code, exchanges it for an access/refresh token pair, then connects
once over the Open API socket to list your trading accounts so you can pick
which ctidTraderAccountId to use for market data.

Saves everything needed for reuse to ctrader_session.json (also not
committed). Run again any time to redo the browser-authorization step (e.g.
if the refresh token is ever revoked).

This never places any order -- it only requests read access to account/
market data (scope="accounts").
"""

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ.get("CTRADER_CLIENT_ID")
CLIENT_SECRET = os.environ.get("CTRADER_CLIENT_SECRET")
REDIRECT_URI = os.environ.get("CTRADER_REDIRECT_URI")

SESSION_PATH = os.path.join(os.path.dirname(__file__), "ctrader_session.json")


def capture_auth_code(port: int) -> str:
    """Runs a tiny local HTTP server just long enough to catch the OAuth
    redirect and pull the `code` query param out of it."""
    code_holder = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            code_holder["code"] = qs.get("code", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Authorized. You can close this tab and return to the terminal.</body></html>")

        def log_message(self, format, *args):
            pass  # keep stdout quiet

    server = HTTPServer(("localhost", port), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    thread.join(timeout=300)
    server.server_close()
    if "code" not in code_holder or code_holder["code"] is None:
        raise RuntimeError("Timed out waiting for the OAuth redirect (5 min). Try again.")
    return code_holder["code"]


def main():
    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
        raise SystemExit(
            "Missing CTRADER_CLIENT_ID / CTRADER_CLIENT_SECRET / CTRADER_REDIRECT_URI.\n"
            "Create a .env file in this directory (D:\\strategytesting) with:\n"
            "  CTRADER_CLIENT_ID=your_client_id\n"
            "  CTRADER_CLIENT_SECRET=your_client_secret\n"
            "  CTRADER_REDIRECT_URI=http://localhost:8090/callback\n"
            "(the redirect URI must exactly match what's registered on your app at "
            "https://openapi.ctrader.com)"
        )

    from ctrader_open_api import Auth

    auth = Auth(CLIENT_ID, CLIENT_SECRET, REDIRECT_URI)
    auth_uri = auth.getAuthUri(scope="accounts")

    print("Opening this URL in your browser -- log in and approve access:")
    print(auth_uri)
    webbrowser.open(auth_uri)

    parsed_redirect = urlparse(REDIRECT_URI)
    if parsed_redirect.hostname not in ("localhost", "127.0.0.1"):
        raise SystemExit("This script only supports a localhost redirect URI for now.")
    port = parsed_redirect.port or 80

    print(f"Waiting for the redirect on {REDIRECT_URI} ...")
    code = capture_auth_code(port)
    print("Got authorization code, exchanging for tokens...")

    token = auth.getToken(code)
    if token.get("errorCode"):
        raise SystemExit(f"Token exchange failed: {token}")

    session = {
        "access_token": token["accessToken"],
        "refresh_token": token["refreshToken"],
        "token_type": token.get("tokenType"),
        "expires_in": token.get("expiresIn"),
    }
    with open(SESSION_PATH, "w") as f:
        json.dump(session, f, indent=2)
    print(f"Saved tokens to {SESSION_PATH}")
    print("Next: run ctrader_list_accounts.py to see which trading account (ctidTraderAccountId) to use.")


if __name__ == "__main__":
    main()
