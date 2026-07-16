"""
PSX Sentinel — PSX Terminal Integration Verification (Phase 5 Session 2)

Runs FundamentalsCollector for real against the live PSX Terminal API
and the live Neon DB, then verifies what actually landed via DIRECT SQL
on a separate synchronous psycopg2 connection — not ORM-cached objects,
not log output.

Usage from the backend/ directory:
    python scripts/verify_psx_terminal.py                 # run + verify
    python scripts/verify_psx_terminal.py --dedup-check   # + 2nd run,
                                                          #   assert 0 new
    python scripts/verify_psx_terminal.py --verify-only   # SQL checks only
                                                          #   (no API calls)

Checks:
1. Collector run summary (tickers processed/failed, rows written).
2. Per ticker: the company_fundamentals row printed field by field,
   with NULLs flagged loudly rather than skipped.
3. Plausibility: pe_ratio in (0, 100), dividend_yield in [0, 30),
   market_cap_pkr > 1e9, free_float_pct in (0, 100], correct types.
4. Announcements with source='psx_terminal': per-ticker counts, date
   ranges, pdf_url coverage, sample titles.
5. The fundamentals_collector PipelineRun row exists with a sane status.
6. (--dedup-check) a second collector run inserts 0 new announcements.
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

EXPECTED_MISSING = {"ENGRO"}  # PSX Terminal only lists post-merger ENGROH

PASS = "[PASS]"
FAIL = "[FAIL]"
WARN = "[WARN]"

failures: list[str] = []
warnings: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    line = f"{PASS if ok else FAIL} {label}"
    if detail:
        line += f" - {detail}"
    print(line)
    if not ok:
        failures.append(label)


def warn(label: str, detail: str = "") -> None:
    line = f"{WARN} {label}"
    if detail:
        line += f" - {detail}"
    print(line)
    warnings.append(label)


async def run_collector(tickers: list[str]) -> dict:
    from app.collectors.fundamentals_collector import FundamentalsCollector
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        return await FundamentalsCollector(db).run_safe(tickers)


def verify_sql(tickers: list[str]) -> None:
    """All verification below uses a fresh sync psycopg2 connection."""
    from sqlalchemy import create_engine, text

    from app.core.config import get_settings

    url = get_settings().DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(url)

    with engine.connect() as conn:
        # ── company_fundamentals, per ticker ──────────────────────────
        print("\n=== company_fundamentals (direct SQL) ===")
        rows = {
            r.ticker: r
            for r in conn.execute(
                text(
                    "SELECT ticker, pe_ratio, dividend_yield, "
                    "market_cap_pkr, free_float_pct, last_updated, source "
                    "FROM company_fundamentals ORDER BY ticker"
                )
            )
        }

        for ticker in tickers:
            row = rows.get(ticker)
            if row is None:
                if ticker in EXPECTED_MISSING:
                    warn(
                        f"{ticker}: no fundamentals row",
                        "expected - PSX Terminal doesn't list this symbol "
                        "(only ENGROH)",
                    )
                else:
                    check(False, f"{ticker}: fundamentals row exists")
                continue

            nulls = [
                f
                for f in (
                    "pe_ratio", "dividend_yield",
                    "market_cap_pkr", "free_float_pct",
                )
                if getattr(row, f) is None
            ]
            print(
                f"  {ticker}: pe={row.pe_ratio} yield={row.dividend_yield} "
                f"mktcap={row.market_cap_pkr} float={row.free_float_pct} "
                f"source={row.source} updated={row.last_updated}"
            )
            if nulls:
                warn(f"{ticker}: NULL fields", ", ".join(nulls))

            # Plausibility — types first, then ranges
            for field in ("pe_ratio", "dividend_yield",
                          "market_cap_pkr", "free_float_pct"):
                v = getattr(row, field)
                if v is not None and not isinstance(v, float):
                    check(
                        False,
                        f"{ticker}.{field} is numeric",
                        f"got {type(v).__name__}: {v!r}",
                    )
            if row.pe_ratio is not None and not (0 < row.pe_ratio < 100):
                warn(f"{ticker}: pe_ratio outside (0,100)", str(row.pe_ratio))
            if row.dividend_yield is not None and not (
                0 <= row.dividend_yield < 30
            ):
                warn(
                    f"{ticker}: dividend_yield outside [0,30)",
                    str(row.dividend_yield),
                )
            if row.market_cap_pkr is not None and row.market_cap_pkr < 1e9:
                warn(
                    f"{ticker}: market_cap_pkr under 1B PKR",
                    str(row.market_cap_pkr),
                )
            if row.free_float_pct is not None and not (
                0 < row.free_float_pct <= 100
            ):
                warn(
                    f"{ticker}: free_float_pct outside (0,100]",
                    str(row.free_float_pct),
                )
            check(
                row.source == "psx_terminal",
                f"{ticker}: source is psx_terminal",
                row.source,
            )

        expected_present = [t for t in tickers if t not in EXPECTED_MISSING]
        present = [t for t in expected_present if t in rows]
        check(
            len(present) == len(expected_present),
            f"fundamentals rows exist for {len(present)}/"
            f"{len(expected_present)} expected tickers",
            f"missing: {sorted(set(expected_present) - set(present))}"
            if len(present) != len(expected_present)
            else "",
        )

        # ── announcements mirror, per ticker ──────────────────────────
        print("\n=== announcements with source='psx_terminal' (direct SQL) ===")
        ann = conn.execute(
            text(
                "SELECT ticker, COUNT(*) AS n, MIN(announced_at) AS oldest, "
                "MAX(announced_at) AS newest, "
                "COUNT(pdf_url) AS with_pdf "
                "FROM announcements WHERE source = 'psx_terminal' "
                "GROUP BY ticker ORDER BY ticker"
            )
        ).fetchall()
        ann_by_ticker = {r.ticker: r for r in ann}
        total_ann = 0
        for ticker in tickers:
            r = ann_by_ticker.get(ticker)
            if r is None:
                if ticker in EXPECTED_MISSING:
                    warn(
                        f"{ticker}: 0 mirrored announcements",
                        "expected - symbol not listed on PSX Terminal",
                    )
                else:
                    warn(f"{ticker}: 0 mirrored announcements")
                continue
            total_ann += r.n
            print(
                f"  {ticker}: {r.n} rows | {r.oldest:%Y-%m-%d} -> "
                f"{r.newest:%Y-%m-%d} | {r.with_pdf}/{r.n} have pdf_url"
            )
        check(total_ann > 0, f"mirrored announcements exist ({total_ann} rows)")

        legacy = conn.execute(
            text(
                "SELECT COUNT(*) FROM announcements "
                "WHERE source IS DISTINCT FROM 'psx_terminal'"
            )
        ).scalar()
        print(f"  (non-psx_terminal announcement rows: {legacy})")

        sample = conn.execute(
            text(
                "SELECT ticker, announced_at, category, LEFT(title, 70) AS title_head, "
                "pdf_url IS NOT NULL AS has_pdf "
                "FROM announcements WHERE source = 'psx_terminal' "
                "ORDER BY announced_at DESC LIMIT 8"
            )
        ).fetchall()
        print("\n  Sample (8 newest):")
        for r in sample:
            print(
                f"    {r.ticker:6} {r.announced_at:%Y-%m-%d} "
                f"[{r.category}] pdf={'Y' if r.has_pdf else 'N'} {r.title_head}"
            )

        # ── pipeline_runs audit row ────────────────────────────────────
        print("\n=== pipeline_runs (direct SQL) ===")
        run = conn.execute(
            text(
                "SELECT pipeline_name, status, tickers_processed, "
                "tickers_failed, started_at, completed_at, error_log "
                "FROM pipeline_runs "
                "WHERE pipeline_name = 'fundamentals_collector' "
                "ORDER BY started_at DESC LIMIT 1"
            )
        ).fetchone()
        check(run is not None, "fundamentals_collector PipelineRun row exists")
        if run is not None:
            print(
                f"  status={run.status} processed={run.tickers_processed} "
                f"failed={run.tickers_failed} "
                f"started={run.started_at} completed={run.completed_at}"
            )
            check(
                run.status in ("SUCCESS", "PARTIAL"),
                "PipelineRun status is SUCCESS/PARTIAL",
                f"{run.status} error_log={run.error_log}",
            )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true",
                        help="Skip the collector run; SQL checks only")
    parser.add_argument("--dedup-check", action="store_true",
                        help="Run the collector twice; assert the second "
                             "pass inserts 0 announcements")
    parser.add_argument("--tickers", type=str, default=None)
    args = parser.parse_args()

    from app.core.config import get_settings

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",")]
        if args.tickers
        else get_settings().tickers_list
    )

    if not args.verify_only:
        print(f"=== Collector run 1 ({len(tickers)} tickers, live API) ===")
        summary = await run_collector(tickers)
        print(f"run 1 summary: {summary}")

        if args.dedup_check:
            print("\n=== Collector run 2 (dedup check) ===")
            summary2 = await run_collector(tickers)
            print(f"run 2 summary: {summary2}")
            check(
                summary2.get("announcements_inserted", -1) == 0,
                "second run inserted 0 announcements (dedup holds)",
                f"inserted {summary2.get('announcements_inserted')}",
            )

    verify_sql(tickers)

    print("\n" + "=" * 60)
    print(
        f"RESULT: {len(failures)} failed, {len(warnings)} warnings"
        + (f" | FAILURES: {failures}" if failures else "")
    )
    print("=" * 60)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
