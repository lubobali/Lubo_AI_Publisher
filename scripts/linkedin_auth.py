#!/usr/bin/env python3
"""LinkedIn OAuth helper — get an access token + person URN, save to .env.

EASY PATH (recommended): use LinkedIn's "OAuth 2.0 tools -> Create token" to get
the access token, paste it into .env as LINKEDIN_ACCESS_TOKEN, then run:
  python3 scripts/linkedin_auth.py urn       # fetch your person URN + expiry into .env

MANUAL PATH (if you prefer the raw flow):
  python3 scripts/linkedin_auth.py url              # print the authorize link
  python3 scripts/linkedin_auth.py exchange <CODE>  # swap code for token, write .env

The person URN comes from /v2/userinfo, which needs the 'openid'/'profile' scope —
add the "Sign In with LinkedIn using OpenID Connect" product to the app.
"""

import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

ENV = Path(__file__).parent.parent / ".env"
load_dotenv(ENV)

import os  # noqa: E402

CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
REDIRECT = os.getenv("LINKEDIN_REDIRECT_URI", "http://localhost:8000/auth/linkedin/callback")
SCOPE = "openid profile w_member_social"


def _set_env(key: str, val: str) -> None:
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    out, found = [], False
    for ln in lines:
        if ln.startswith(key + "="):
            out.append(f"{key}={val}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{key}={val}")
    ENV.write_text("\n".join(out) + "\n")


def cmd_url() -> None:
    if not CLIENT_ID:
        sys.exit("LINKEDIN_CLIENT_ID missing in .env")
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT,
        "scope": SCOPE,
        "state": secrets.token_hex(8),
    }
    print("\n1) Open this in your browser and click Allow:\n")
    print("https://www.linkedin.com/oauth/v2/authorization?" + urlencode(params))
    print(f"\n2) The browser will then try to load:\n   {REDIRECT}?code=XXXXXX&state=...")
    print("   It will fail to load (nothing runs there) — that is FINE.")
    print("   Copy the long 'code=' value from the URL bar.\n")
    print("3) Run:  python3 scripts/linkedin_auth.py exchange <that-code>\n")


def _fetch_urn(access: str) -> str:
    headers = {"Authorization": f"Bearer {access}"}
    # Newer OpenID Connect scope -> /v2/userinfo returns 'sub'
    u = httpx.get("https://api.linkedin.com/v2/userinfo", headers=headers, timeout=30)
    if u.status_code == 200 and u.json().get("sub"):
        return f"urn:li:person:{u.json()['sub']}"
    # Older r_liteprofile scope -> /v2/me returns 'id'
    m = httpx.get("https://api.linkedin.com/v2/me", headers=headers, timeout=30)
    if m.status_code == 200 and m.json().get("id"):
        return f"urn:li:person:{m.json()['id']}"
    sys.exit(
        f"Could not get your URN (userinfo={u.status_code}, me={m.status_code}). "
        "Add the 'Sign In with LinkedIn' product, then create a NEW token with the "
        "profile/openid scope checked, and paste that one."
    )


def cmd_urn() -> None:
    """Token already in .env (from the Create token tool) — fetch + save the URN + expiry."""
    access = os.getenv("LINKEDIN_ACCESS_TOKEN")
    if not access:
        sys.exit("Put your token in .env first:  LINKEDIN_ACCESS_TOKEN=<token from the Create token tool>")
    urn = _fetch_urn(access)
    _set_env("LINKEDIN_PERSON_URN", urn)
    _set_env("LINKEDIN_TOKEN_EXPIRES_AT", (datetime.now(UTC) + timedelta(seconds=5184000)).isoformat())
    print(f"\n✅ person URN saved: {urn}")
    print("Now reload the worker:  docker compose restart publisher-worker\n")


def cmd_exchange(code: str) -> None:
    if not (CLIENT_ID and CLIENT_SECRET):
        sys.exit("LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET missing in .env")

    r = httpx.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT,
        },
        timeout=30,
    )
    if r.status_code != 200:
        sys.exit(f"Token exchange failed ({r.status_code}): {r.text}")
    tok = r.json()
    access = tok["access_token"]
    expires_in = int(tok.get("expires_in", 5184000))

    urn = ""
    try:
        u = httpx.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {access}"},
            timeout=30,
        )
        if u.status_code == 200 and u.json().get("sub"):
            urn = f"urn:li:person:{u.json()['sub']}"
    except Exception:
        pass

    expires_at = (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()
    _set_env("LINKEDIN_ACCESS_TOKEN", access)
    _set_env("LINKEDIN_TOKEN_EXPIRES_AT", expires_at)
    if urn:
        _set_env("LINKEDIN_PERSON_URN", urn)

    print("\n✅ Access token saved to .env")
    print("   person URN:", urn or "NOT obtained — add the OpenID Connect product, or set LINKEDIN_PERSON_URN by hand")
    print("   expires:", expires_at)
    print("\nThen reload the worker:  docker compose restart publisher-worker\n")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "url":
        cmd_url()
    elif len(sys.argv) >= 2 and sys.argv[1] == "urn":
        cmd_urn()
    elif len(sys.argv) >= 3 and sys.argv[1] == "exchange":
        cmd_exchange(sys.argv[2])
    else:
        sys.exit(__doc__)
