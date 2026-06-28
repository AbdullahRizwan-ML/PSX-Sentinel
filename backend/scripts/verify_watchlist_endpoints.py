"""
Phase 4 Session 4 -- Step 0: live verification of /watchlist endpoints.

Read-write script. Registers a fresh user (timestamped email so reruns
don't collide), then walks through every documented response code for
the three watchlist endpoints against a running backend at
http://localhost:8000:

    GET    /api/v1/watchlist               (empty)
    POST   /api/v1/watchlist                (success -> 201)
    POST   /api/v1/watchlist                (duplicate -> 409)
    POST   /api/v1/watchlist                (unknown ticker -> 404)
    GET    /api/v1/watchlist                (after add)
    DELETE /api/v1/watchlist/{ticker}       (success -> 200)
    DELETE /api/v1/watchlist/{ticker}       (nonexistent -> 404)
    GET    /api/v1/watchlist                (after remove -> empty)

Prints PASS / FAIL per check so we have a clean "before frontend" baseline.
"""

import sys
import time
from typing import Any

# Windows terminal can't render unicode arrows in cp1252 -- see CLAUDE.md
# "Known environment quirks". Use ASCII throughout.

import httpx

BASE = "http://localhost:8000"
PASS = "[OK]"
FAIL = "[FAIL]"
results: list[tuple[bool, str]] = []


def record(ok: bool, label: str, detail: str = "") -> None:
    tag = PASS if ok else FAIL
    suffix = f" -- {detail}" if detail else ""
    print(f"  {tag} {label}{suffix}")
    results.append((ok, label))


def main() -> int:
    email = f"watchlist_step0_{int(time.time())}@example.com"
    print(f"\n== Register {email} ==")
    with httpx.Client(base_url=BASE, timeout=30.0) as c:
        r = c.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "TestPass123!",
                "full_name": "Watchlist Step0",
            },
        )
        record(r.status_code == 201, f"register -> {r.status_code}", r.text[:200])
        if r.status_code != 201:
            return 1
        token = r.json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        print("\n== 1. GET /watchlist (empty) ==")
        r = c.get("/api/v1/watchlist", headers=h)
        body: Any = r.json() if r.text else None
        record(
            r.status_code == 200 and body == [],
            f"GET watchlist empty -> {r.status_code}",
            f"body={body!r}",
        )

        print("\n== 2. POST /watchlist PPL (first add) ==")
        r = c.post(
            "/api/v1/watchlist",
            json={"ticker": "PPL", "notes": "step0 first add"},
            headers=h,
        )
        record(
            r.status_code == 201,
            f"POST watchlist PPL -> {r.status_code}",
            r.text[:300],
        )
        first_id = r.json().get("id") if r.status_code == 201 else None
        record(
            r.json().get("ticker") == "PPL" if r.status_code == 201 else False,
            "response ticker == 'PPL'",
        )
        record(
            r.json().get("company_name") is not None
            if r.status_code == 201
            else False,
            "response includes company_name",
            f"company_name={r.json().get('company_name')!r}"
            if r.status_code == 201
            else "",
        )

        print("\n== 3. POST /watchlist PPL again (duplicate) ==")
        r = c.post(
            "/api/v1/watchlist",
            json={"ticker": "PPL"},
            headers=h,
        )
        record(
            r.status_code == 409,
            f"duplicate add -> {r.status_code} (expect 409)",
            r.text[:300],
        )

        print("\n== 4. POST /watchlist with unknown ticker ==")
        r = c.post(
            "/api/v1/watchlist",
            json={"ticker": "NOPE"},
            headers=h,
        )
        record(
            r.status_code == 404,
            f"unknown ticker -> {r.status_code} (expect 404)",
            r.text[:300],
        )

        print("\n== 5. POST /watchlist mcb (lowercase, should normalize) ==")
        r = c.post(
            "/api/v1/watchlist",
            json={"ticker": "mcb"},
            headers=h,
        )
        record(
            r.status_code == 201
            and r.json().get("ticker") == "MCB",
            f"lowercase mcb -> {r.status_code}, ticker={r.json().get('ticker')!r}",
        )

        print("\n== 6. GET /watchlist (after 2 adds) ==")
        r = c.get("/api/v1/watchlist", headers=h)
        items = r.json() if r.text else []
        tickers = sorted([i["ticker"] for i in items])
        record(
            r.status_code == 200 and tickers == ["MCB", "PPL"],
            f"GET watchlist -> {r.status_code}, tickers={tickers}",
        )

        print("\n== 7. DELETE /watchlist/PPL ==")
        r = c.delete("/api/v1/watchlist/PPL", headers=h)
        record(
            r.status_code == 200,
            f"DELETE PPL -> {r.status_code}",
            r.text[:200],
        )

        print("\n== 8. DELETE /watchlist/PPL again (already removed) ==")
        r = c.delete("/api/v1/watchlist/PPL", headers=h)
        record(
            r.status_code == 404,
            f"DELETE nonexistent -> {r.status_code} (expect 404)",
            r.text[:200],
        )

        print("\n== 9. DELETE /watchlist/mcb (lowercase, should normalize) ==")
        r = c.delete("/api/v1/watchlist/mcb", headers=h)
        record(
            r.status_code == 200,
            f"DELETE lowercase mcb -> {r.status_code}",
            r.text[:200],
        )

        print("\n== 10. GET /watchlist (empty again) ==")
        r = c.get("/api/v1/watchlist", headers=h)
        body = r.json()
        record(
            r.status_code == 200 and body == [],
            f"final empty -> {r.status_code}, body={body!r}",
        )

    print("\n" + "=" * 50)
    n_pass = sum(1 for ok, _ in results if ok)
    n_fail = sum(1 for ok, _ in results if not ok)
    print(f"  PASS: {n_pass}    FAIL: {n_fail}")
    print("=" * 50)
    _ = first_id  # silence unused
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
