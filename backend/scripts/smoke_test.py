#!/usr/bin/env python3
"""Post-deploy smoke test — verifies the critical paths + the media-security fix
against a LIVE deployment. Read-only (no test data created), stdlib-only.

Usage:
  python scripts/smoke_test.py --base-url https://nif.example.com \
      --email admin@nif.org.np --password '...'
  (or set SMOKE_BASE_URL / SMOKE_EMAIL / SMOKE_PASSWORD)

Exit code 0 = all pass, 1 = a check failed. Automatable in CD after a deploy.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def _req(method, url, token=None, data=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        resp = urllib.request.urlopen(r, timeout=20)
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default=os.getenv("SMOKE_BASE_URL", "http://localhost:8001"))
    p.add_argument("--email", default=os.getenv("SMOKE_EMAIL"))
    p.add_argument("--password", default=os.getenv("SMOKE_PASSWORD"))
    a = p.parse_args()
    base = a.base_url.rstrip("/")
    api = f"{base}/api/v1"
    checks, ok = [], True

    def check(name, cond):
        nonlocal ok
        checks.append((name, "PASS" if cond else "FAIL"))
        ok = ok and cond

    # 1. Health
    st, _ = _req("GET", f"{api}/health/")
    check("health 200", st == 200)

    # 2. Security: legacy public media route is gone; unsigned media denied
    st, _ = _req("GET", f"{base}/media/memos/x.pdf")
    check("raw /media/ closed (404)", st == 404)
    st, _ = _req("GET", f"{api}/media/?p=memos/x.pdf")
    check("unsigned /api/v1/media/ denied (403)", st == 403)

    # 3. Auth: login
    token = None
    if a.email and a.password:
        st, d = _req("POST", f"{api}/auth/login/", data={"email": a.email, "password": a.password})
        token = (d or {}).get("access")
        check("login returns access token", st == 200 and bool(token))
    else:
        checks.append(("login (skipped: no creds)", "SKIP"))

    # 4. Authenticated critical reads
    if token:
        for name, path in [
            ("leave actionable queue", "/leaves/?queue=actionable"),
            ("leave policy", "/leaves/leave-policy/"),
            ("inventory items", "/inventory/items/"),
            ("memos list", "/memos/"),
            ("notifications unread-count", "/notifications/unread-count/"),
        ]:
            st, _ = _req("GET", f"{api}{path}", token=token)
            check(name + " 200", st == 200)

    print("\n  Post-deploy smoke test")
    print("  " + "-" * 40)
    for name, res in checks:
        print(f"  [{res}] {name}")
    print()
    if not ok:
        print("SMOKE TEST FAILED")
        sys.exit(1)
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
