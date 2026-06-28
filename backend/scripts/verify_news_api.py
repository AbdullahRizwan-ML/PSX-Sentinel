"""
Phase 4 Session 5 — end-to-end API verification.

Exercises the same HTTP paths the frontend NewsList component takes:
register a fresh user, get a token, then for each ticker that has a
persisted IntelligenceReport, confirm that:

  1. GET /api/v1/companies/{ticker}/report serves the new
     `news_synthesis` field with the expected article_count /
     relevant_articles, AND still serves the old `score_breakdown`
     field correctly (regression guard).
  2. GET /api/v1/companies/{ticker}/news returns a paginated payload
     whose `total` matches `news_synthesis.article_count` from the
     report (sanity-checks the two sources agree on what counts as
     "matched").

Busts the Redis-cached report key for each test ticker before the
read, so the new field is guaranteed visible even if a cached
pre-change response is still in Redis.

Read-write — registers one timestamped test user, no other writes.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.redis_client import REPORT_CACHE_KEY, redis_client  # noqa: E402


BASE = "http://127.0.0.1:8000"
TICKERS = ["PPL", "MCB", "UBL"]  # tickers with persisted reports


async def bust_report_caches() -> list[str]:
    """Delete today's cached report keys for our test tickers."""
    from datetime import date as _date

    today = _date.today().isoformat()
    busted: list[str] = []
    for tkr in TICKERS:
        key = REPORT_CACHE_KEY.format(ticker=tkr, date=today)
        try:
            ok = await redis_client.delete_cached(key)
            if ok:
                busted.append(key)
        except Exception as exc:
            print(f"  warn: couldn't delete {key}: {exc}")
    return busted


async def register_and_login(client: httpx.AsyncClient) -> str:
    suffix = str(int(time.time()))
    # `.test` is reserved per RFC 6761 and python-email-validator's
    # default deliverability check rejects it; use example.com which
    # is RFC 2606 reserved-for-documentation and passes the check.
    email = f"news_api_test_{suffix}@example.com"
    password = "VerifyNews$2026!"

    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "full_name": "News API Test",
        },
    )
    r.raise_for_status()
    token = r.json()["access_token"]
    return token


async def main() -> int:
    print("=== Phase 4 Session 5 -- /report + /news live verification ===\n")

    # 1. Bust report caches so the new news_synthesis field is visible.
    print("1. Busting Redis report caches for test tickers")
    busted = await bust_report_caches()
    if busted:
        for k in busted:
            print(f"   - deleted {k}")
    else:
        print("   (no cached keys to delete; either fresh or already expired)")

    failures: list[str] = []

    async with httpx.AsyncClient(base_url=BASE, timeout=15.0) as client:
        print("\n2. Registering fresh user")
        token = await register_and_login(client)
        headers = {"Authorization": f"Bearer {token}"}
        print(f"   OK (token len={len(token)})")

        for ticker in TICKERS:
            print(f"\n3. {ticker}")

            r = await client.get(
                f"/api/v1/companies/{ticker}/report", headers=headers
            )
            if r.status_code != 200:
                failures.append(
                    f"{ticker} /report → {r.status_code}: {r.text[:200]}"
                )
                print(f"   FAIL /report status={r.status_code}")
                continue
            rep = r.json()

            ns = rep.get("news_synthesis")
            sb = rep.get("score_breakdown")
            print(
                f"   /report: news_synthesis={'present' if ns else 'MISSING'}"
                f" score_breakdown={'present' if sb else 'MISSING'}"
            )
            if ns is None:
                failures.append(
                    f"{ticker}: /report response missing news_synthesis"
                )
            else:
                print(
                    "   news_synthesis = "
                    + json.dumps(
                        {
                            "sentiment": ns.get("sentiment"),
                            "article_count": ns.get("article_count"),
                            "relevant_articles": ns.get("relevant_articles"),
                            "uniformity": ns.get("uniformity"),
                        }
                    )
                )

            if sb is None:
                failures.append(
                    f"{ticker}: /report response missing score_breakdown "
                    f"(regression — Session 2 wired this)"
                )

            r2 = await client.get(
                f"/api/v1/companies/{ticker}/news?limit=50", headers=headers
            )
            if r2.status_code != 200:
                failures.append(
                    f"{ticker} /news → {r2.status_code}: {r2.text[:200]}"
                )
                print(f"   FAIL /news status={r2.status_code}")
                continue
            news_payload = r2.json()
            total_from_api = news_payload.get("total", 0)
            items_returned = len(news_payload.get("items", []))
            print(
                f"   /news: total={total_from_api}"
                f" items_in_page={items_returned}"
            )

            if ns is not None:
                ac = ns.get("article_count")
                # NewsSynthesizer's article_count is the number of
                # articles it received as input, which is the same set
                # /news returns now. They should match. (Strictly the
                # NewsSynthesizer cap is currently article_count = len(
                # context.news_articles), with no slicing on the
                # context-build side, so equality is expected today.)
                if ac is not None and ac != total_from_api:
                    failures.append(
                        f"{ticker}: news_synthesis.article_count={ac} "
                        f"!= /news.total={total_from_api}"
                    )
                else:
                    print(
                        f"   cross-check: article_count == /news.total "
                        f"({ac})  OK"
                    )

            # Classify the zero-state for the human-readable summary.
            if ns is not None:
                ac = ns.get("article_count", 0)
                rel = ns.get("relevant_articles", 0)
                if ac == 0:
                    mode = "NO_ARTICLES_MATCHED -> frontend zero-state 1"
                elif rel == 0:
                    mode = "MATCHED_BUT_NONE_RELEVANT -> frontend zero-state 2"
                elif rel < ac:
                    mode = (
                        f"PARTIAL_RELEVANCE ({rel}/{ac}) -> frontend "
                        f"zero-state 3 (untestable in real data today)"
                    )
                else:
                    mode = (
                        f"FULL_RELEVANCE ({rel}/{ac}) -> frontend list "
                        f"(untestable in real data today)"
                    )
                print(f"   => render mode: {mode}")

    print()
    if failures:
        print(f"=== {len(failures)} FAILURE(S) ===")
        for msg in failures:
            print(f"  - {msg}")
        return 1
    print("=== All API checks passed ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
