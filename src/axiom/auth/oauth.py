import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from axiom.connectors.apps.base import AppConnectorManifest


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def connect_oauth2_pkce(manifest: AppConnectorManifest) -> dict:
    """
    Runs the OAuth2 PKCE loopback flow synchronously (designed for CLI use).
    Returns raw credentials dict: {access_token, refresh_token, expires_at, ...}.
    """
    assert manifest.oauth2 is not None, f"{manifest.name} has no oauth2 config"
    oauth = manifest.oauth2

    client_id = os.environ.get(oauth.client_id_env, "")
    client_secret = os.environ.get(oauth.client_secret_env, "")
    if not client_id:
        raise EnvironmentError(
            f"Missing environment variable: {oauth.client_id_env}\n"
            f"Export it before running: axiom connect {manifest.name}"
        )

    port = oauth.redirect_port
    redirect_uri = f"http://localhost:{port}/callback"
    verifier, challenge = _pkce_pair()
    state_token = secrets.token_urlsafe(16)

    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(oauth.scopes),
        "state": state_token,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    })
    auth_url = f"{oauth.auth_url}?{params}"

    print(f"\n[axiom] Connecting {manifest.display_name}...")
    print(f"[axiom] Open this URL in your browser:\n\n  {auth_url}\n")

    try:
        import webbrowser
        webbrowser.open(auth_url)
    except Exception:
        pass

    received: dict = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            received.update(urllib.parse.parse_qsl(parsed.query))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Axiom: authorization received."
                b" You may close this tab.</h2></body></html>"
            )

        def log_message(self, *_):
            pass

    print(f"[axiom] Waiting for authorization (port {port})...")
    server = HTTPServer(("localhost", port), _Handler)

    # Run the server in a background thread so we can loop until the
    # actual callback (with the code) arrives — the browser often makes
    # extra requests (favicon, pre-flight) before the real one.
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    deadline = time.time() + 120
    while time.time() < deadline:
        if received.get("code"):
            break
        time.sleep(0.2)

    server.shutdown()

    code = received.get("code")
    if not code:
        raise RuntimeError(f"No authorization code received. Response was: {received}")
    if received.get("state") != state_token:
        raise RuntimeError("OAuth state mismatch — possible CSRF. Aborting.")

    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
        "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(oauth.token_url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req, timeout=15) as resp:
        tokens = json.loads(resp.read())

    return {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "expires_at": time.time() + tokens.get("expires_in", 3600),
        "token_type": tokens.get("token_type", "Bearer"),
        "scope": tokens.get("scope", ""),
    }


def connect_api_key(manifest: AppConnectorManifest, api_key: str) -> dict:
    """Stores an API key as the connector credential."""
    return {"api_key": api_key}
