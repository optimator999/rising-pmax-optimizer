"""Generate Google Ads API refresh token via OAuth2 flow.

This script:
1. Pulls client_id and client_secret from AWS Parameter Store
2. Opens a browser for Google authorization
3. Captures the auth code and exchanges it for a refresh token
4. Stores the refresh token in Parameter Store
"""

import json
import sys
import urllib.parse
import urllib.request
import urllib.error
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler

import boto3

REGION = "us-east-2"
SCOPES = "https://www.googleapis.com/auth/adwords"
REDIRECT_URI = "http://localhost:8080"

ssm = boto3.client("ssm", region_name=REGION)


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def main():
    print("Fetching credentials from Parameter Store...")
    client_id = get_param("/Google_Ads/CLIENT_ID")
    client_secret = get_param("/Google_Ads/CLIENT_SECRET")
    print("Got client_id and client_secret.\n")

    # Build authorization URL
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": SCOPES,
                "access_type": "offline",
                "prompt": "consent",
            }
        )
    )

    print("Opening browser for authorization...")
    print(f"If it doesn't open, go to:\n{auth_url}\n")
    webbrowser.open(auth_url)

    # Start local server to capture the redirect
    auth_code = None

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            auth_code = params.get("code", [None])[0]

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<h1>Authorization successful!</h1>"
                b"<p>You can close this tab and return to the terminal.</p>"
            )

        def log_message(self, format, *args):
            pass  # Suppress server logs

    print("Waiting for authorization (listening on localhost:8080)...")
    server = HTTPServer(("localhost", 8080), Handler)
    server.handle_request()

    if not auth_code:
        print("ERROR: No authorization code received.")
        sys.exit(1)

    print("Got authorization code. Exchanging for refresh token...")

    # Exchange auth code for tokens
    token_data = urllib.parse.urlencode(
        {
            "code": auth_code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    ).encode()

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=token_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib.request.urlopen(req) as resp:
            tokens = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"ERROR: Token exchange failed: {error_body}")
        sys.exit(1)

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("ERROR: No refresh token in response. Tokens received:")
        print(json.dumps(tokens, indent=2))
        sys.exit(1)

    print(f"Got refresh token: {refresh_token[:20]}...")

    # Store in Parameter Store
    print("Saving to Parameter Store at /Google_Ads/REFRESH_TOKEN...")
    ssm.put_parameter(
        Name="/Google_Ads/REFRESH_TOKEN",
        Value=refresh_token,
        Type="SecureString",
        Overwrite=True,
    )

    print("\nDone! Refresh token saved to Parameter Store.")
    print("You can now run the Lambda function.")


if __name__ == "__main__":
    main()
