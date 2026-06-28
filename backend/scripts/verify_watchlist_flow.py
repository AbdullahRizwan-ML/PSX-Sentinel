"""
Phase 4 Session 4 -- Step 7: live end-to-end verification of the
watchlist user flow against the running backend.

Walks the same HTTP path the frontend takes:

    1. Register fresh test user.
    2. Simulate "dashboard mount":
        GET /api/v1/companies (universe list)
        GET /api/v1/watchlist (context first-load, expect empty)
    3. Simulate "click star on PPL" (add path):
        POST /api/v1/watchlist {"ticker": "PPL"}
        GET  /api/v1/watchlist -> assert PPL present
        Membership-Set check (the dashboard filter): assert PPL in set.
    4. Simulate "click star on MCB" (second add).
    5. Simulate "switch to My Watchlist tab" -- pure client filtering,
       but assert the intersection of the universe and the watchlist
       Set gives the right list of tickers.
    6. Simulate "click star on PPL again" (remove path):
        DELETE /api/v1/watchlist/PPL -> 200
        GET    /api/v1/watchlist     -> assert PPL gone, MCB still present.
    7. Optimistic-rollback dry-run for the "add when already added"
       case -- the provider treats 409 as idempotent success and keeps
       the optimistic insert. Confirm 409 is the actual server status.
    8. Optimistic-rollback dry-run for the "remove when not present"
       case -- the provider treats 404 as idempotent success.
       Confirm 404.

Prints PASS / FAIL per check. Exit nonzero on any FAIL so the script
can be run from CI later.
"""

import sys
import time
from typing import Any

import httpx

BASE = "http://localhost:8000"
results: list[tuple[bool, str]] = []


def record(ok: bool, label: str, detail: str = "") -> None:
    tag = "[OK]" if ok else "[FAIL]"
    suffix = f"  -- {detail}" if detail else ""
    print(f"  {tag} {label}{suffix}")
    results.append((ok, label))


def main() -> int:
    email = f"watchlist_flow_{int(time.time())}@example.com"
    print(f"\n== Register {email} ==")
    with httpx.Client(base_url=BASE, timeout=30.0) as c:
        r = c.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "TestPass123!",
                "full_name": "Watchlist Flow",
            },
        )
        record(r.status_code == 201, f"register -> {r.status_code}", r.text[:120])
        if r.status_code != 201:
            return 1
        token = r.json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        print("\n== Step 1. Dashboard mount: list universe ==")
        r = c.get("/api/v1/companies?limit=50", headers=h)
        universe_payload: Any = r.json()
        universe_items = universe_payload.get("items", []) if isinstance(universe_payload, dict) else []
        universe_tickers = sorted([i["ticker"] for i in universe_items])
        record(
            r.status_code == 200 and len(universe_tickers) >= 10,
            f"GET /companies -> {r.status_code}, n={len(universe_tickers)}",
            f"tickers={universe_tickers[:5]}...",
        )

        print("\n== Step 2. Watchlist context first-load (empty) ==")
        r = c.get("/api/v1/watchlist", headers=h)
        record(
            r.status_code == 200 and r.json() == [],
            f"GET /watchlist initial -> {r.status_code}, body={r.json()!r}",
        )

        print("\n== Step 3. Click star on PPL (add) ==")
        r = c.post(
            "/api/v1/watchlist", json={"ticker": "PPL"}, headers=h
        )
        record(r.status_code == 201, f"POST PPL -> {r.status_code}", r.text[:120])

        r = c.get("/api/v1/watchlist", headers=h)
        watchlist = r.json()
        ticker_set = {w["ticker"] for w in watchlist}
        record(
            "PPL" in ticker_set,
            f"membership set after add: {sorted(ticker_set)}",
        )

        print("\n== Step 4. Click star on MCB (second add) ==")
        r = c.post(
            "/api/v1/watchlist", json={"ticker": "MCB"}, headers=h
        )
        record(r.status_code == 201, f"POST MCB -> {r.status_code}")

        r = c.get("/api/v1/watchlist", headers=h)
        ticker_set = {w["ticker"] for w in r.json()}
        record(
            ticker_set == {"PPL", "MCB"},
            f"set after 2 adds: {sorted(ticker_set)}",
        )

        print("\n== Step 5. Switch to My Watchlist tab (client filter) ==")
        # The dashboard filter is client-side: intersect the universe
        # with the watchlist set.
        filtered = [t for t in universe_tickers if t in ticker_set]
        record(
            sorted(filtered) == ["MCB", "PPL"],
            f"filtered universe = {filtered}",
        )

        print("\n== Step 6. Click star on PPL again (remove) ==")
        r = c.delete("/api/v1/watchlist/PPL", headers=h)
        record(r.status_code == 200, f"DELETE PPL -> {r.status_code}", r.text[:120])

        r = c.get("/api/v1/watchlist", headers=h)
        ticker_set = {w["ticker"] for w in r.json()}
        record(
            ticker_set == {"MCB"},
            f"set after remove: {sorted(ticker_set)} (expect ['MCB'])",
        )

        print("\n== Step 7. Optimistic-rollback dry-run: 409 on dup-add ==")
        # MCB already on watchlist; second add should return 409 so the
        # provider's "treat as idempotent success" branch is exercised
        # for real. This is what the frontend test will rely on.
        r = c.post("/api/v1/watchlist", json={"ticker": "MCB"}, headers=h)
        record(
            r.status_code == 409,
            f"dup add -> {r.status_code} (expect 409 for idempotence)",
            r.text[:120],
        )

        print("\n== Step 8. Optimistic-rollback dry-run: 404 on remove-missing ==")
        r = c.delete("/api/v1/watchlist/PPL", headers=h)
        record(
            r.status_code == 404,
            f"remove-missing -> {r.status_code} (expect 404 for idempotence)",
            r.text[:120],
        )

        print("\n== Step 9. Optimistic-rollback dry-run: ROLLBACK case ==")
        # Real-error case where the provider MUST roll back the
        # optimistic insert: 404 on add (unknown ticker). This won't
        # be hit on the dashboard since the universe is constrained,
        # but the company-detail header could in principle if someone
        # crafts a URL by hand. Confirm 404 fires.
        r = c.post(
            "/api/v1/watchlist", json={"ticker": "ZZZNOPE"}, headers=h
        )
        record(
            r.status_code == 404,
            f"unknown ticker add -> {r.status_code} (expect 404 -> rollback)",
            r.text[:120],
        )

        print("\n== Step 10. Cleanup ==")
        r = c.delete("/api/v1/watchlist/MCB", headers=h)
        record(r.status_code == 200, f"cleanup DELETE MCB -> {r.status_code}")
        r = c.get("/api/v1/watchlist", headers=h)
        record(
            r.json() == [],
            f"final empty -> {r.json()!r}",
        )

    print("\n" + "=" * 50)
    n_pass = sum(1 for ok, _ in results if ok)
    n_fail = sum(1 for ok, _ in results if not ok)
    print(f"  PASS: {n_pass}    FAIL: {n_fail}")
    print("=" * 50)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
