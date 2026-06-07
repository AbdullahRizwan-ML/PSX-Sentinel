"""
PSX Sentinel — Data Source Diagnostic v2

Tests all confirmed-working data sources:
1. PSX DPS timeseries API (prices)
2. ARY News Business RSS (news)

Run from the backend/ directory:
    python scripts/diagnose_sources.py
"""

import asyncio
from datetime import datetime, timedelta, timezone

import feedparser
import httpx


async def test_all():
    print("=" * 60)
    print("PSX SENTINEL — DATA SOURCE DIAGNOSTIC v2")
    print("=" * 60)

    # ── Test 1: PSX DPS timeseries (confirmed working) ────────────────
    print("\n[1] PSX DPS timeseries — ENGRO (should work):")
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            end = datetime.now(timezone.utc).date()
            start = end - timedelta(days=30)
            r = await client.get(
                "https://dps.psx.com.pk/timeseries/eod/ENGRO",
                params={
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                },
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://dps.psx.com.pk/",
                },
            )
            print(f"   Status : {r.status_code}")
            if r.status_code == 200:
                data = r.json().get("data", [])
                print(f"   Rows   : {len(data)}")
                if data:
                    print(f"   Sample : {data[0]}")
            else:
                print(f"   Body   : {r.text[:200]}")
        except Exception as e:
            print(f"   Error  : {e}")

    # ── Test 2: ARY News RSS (confirmed working) ──────────────────────
    print("\n[2] ARY News Business RSS (should work):")
    async with httpx.AsyncClient(
        timeout=20.0, follow_redirects=True
    ) as client:
        try:
            r = await client.get(
                "https://arynews.tv/category/business/feed/",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            print(f"   Status       : {r.status_code}")
            print(
                f"   Content-Type : "
                f"{r.headers.get('content-type', 'unknown')}"
            )
            if r.status_code == 200:
                feed = feedparser.parse(r.text)
                print(f"   Entries      : {len(feed.entries)}")
                print(f"   Bozo         : {feed.bozo}")
                if feed.entries:
                    title = feed.entries[0].get("title", "N/A")[:80]
                    print(f"   Headline #1  : {title}")
            else:
                print(f"   Body (200)   : {r.text[:200]}")
        except Exception as e:
            print(f"   Error  : {e}")

    print("\n" + "=" * 60)
    print("DIAGNOSTIC v2 COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_all())
