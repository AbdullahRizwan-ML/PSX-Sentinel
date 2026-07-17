"""
PSX Sentinel — Historical Depth Extension Verification (Phase 5 Session 5)

Read-only. Verifies the session's two deliverables via DIRECT SQL on a
fresh sync psycopg2 connection:

1. daily_prices — every ACTIVE ticker reaches the current right edge of
   PSX DPS's rolling window; ENGRO stays frozen at 2025-01-03 / 887 rows;
   cross-ticker trading-day consistency over the shared window (a date
   missing for one ticker but present for the other nine is a gap; a
   date absent for everyone is a holiday and fine).

2. institutional_flows — all 4 NCCPL datasets cover the matched window
   (2021-06-07 onward); flow trading days line up with price trading
   days (the two sources should agree on what a PSX trading day is);
   no TOTAL rollups / '---' separators / blank client types; dedup
   constraint intact.

Context: PSX DPS serves a fixed rolling ~5-year window and ignores all
date parameters (verified live 2026-07-17, see the price_collector
docstring), so "full depth" means "everything the source offers plus
older rows we captured before they rolled off". 7-8 years is not
achievable from this source.

    python scripts/verify_depth_extension.py
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = "[PASS]", "[FAIL]"
failures: list[str] = []

# The matched target window locked in Part 1 of the session.
WINDOW_START = date(2021, 6, 7)

ACTIVE = ["ENGROH", "LUCK", "OGDC", "PPL", "MCB",
          "HBL", "UBL", "MARI", "PSO", "MEBL"]


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"{PASS if ok else FAIL} {label}" + (f" - {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    from sqlalchemy import create_engine, text
    from app.core.config import get_settings

    url = get_settings().DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(url)

    with engine.connect() as c:
        # ── 1. daily_prices ─────────────────────────────────────────────
        print("=== daily_prices ===")
        rows = c.execute(text(
            "SELECT ticker, COUNT(*) n, MIN(date) lo, MAX(date) hi "
            "FROM daily_prices GROUP BY ticker ORDER BY ticker"
        )).fetchall()
        per = {r.ticker: r for r in rows}
        for r in rows:
            print(f"  {r.ticker:8} {r.n:5} rows  {r.lo} -> {r.hi}")

        # all active tickers share the same right edge (the latest PSX
        # trading day any of them has)
        right_edges = {per[t].hi for t in ACTIVE if t in per}
        check(len(right_edges) == 1,
              "all 10 active tickers share one right edge",
              f"edges={sorted(right_edges)}")

        engro = per.get("ENGRO")
        check(engro is not None
              and engro.n == 887
              and str(engro.hi) == "2025-01-03",
              "ENGRO frozen (887 rows, ends 2025-01-03)",
              f"got {engro.n if engro else 0} rows, ends {engro.hi if engro else None}")

        # Per-ticker "holes": a date >= 9 of the 10 active tickers traded
        # but this one didn't, inside its own span. NOTE (Phase 5 Session
        # 5): a hole is NOT automatically a collection defect. PSX DPS is
        # authoritative, and a thinly-traded name legitimately has
        # no-print days the blue chips don't. Confirmed live against the
        # source this session: all 17 ENGROH holes (ex-Dawood Hercules,
        # illiquid holding co — scattered single days) and all 5 LUCK
        # holes (a contiguous 2025-04-21..25 run, a trading suspension
        # around LUCK's 2025-04-28 5:1 split) are ABSENT from PSX DPS
        # itself — our per-ticker row counts byte-match what the source
        # serves. So holes are reported as INFORMATIONAL. The real
        # collection-failure signature is a *long contiguous* missing run
        # (like a mid-run collector crash), so that is the hard check.
        gaps = c.execute(text("""
            WITH active AS (
                SELECT ticker, date FROM daily_prices
                WHERE ticker = ANY(:active)
            ), spans AS (
                SELECT ticker, MIN(date) lo, MAX(date) hi
                FROM active GROUP BY ticker
            ), day_counts AS (
                SELECT date, COUNT(*) n FROM active GROUP BY date
            )
            SELECT s.ticker, d.date
            FROM spans s
            JOIN day_counts d
              ON d.date BETWEEN s.lo AND s.hi AND d.n >= 9
            LEFT JOIN active a
              ON a.ticker = s.ticker AND a.date = d.date
            WHERE a.date IS NULL
            ORDER BY s.ticker, d.date
        """), {"active": ACTIVE}).fetchall()
        from collections import Counter
        holes_by_ticker = Counter(g.ticker for g in gaps)
        print(f"  per-ticker no-trade days inside span (informational — "
              f"source-confirmed non-trading, not gaps): "
              f"{dict(holes_by_ticker) or 'none'}")

        # hard check: longest run of consecutive index-trading days a
        # single ticker is missing. A genuine illiquid gap or a
        # corporate-action suspension is short (LUCK's split window = 5);
        # a collection failure would leave a long run. Threshold 10.
        MAX_TOLERATED_RUN = 10
        index_days = [r.date for r in c.execute(text("""
            WITH active AS (SELECT ticker, date FROM daily_prices
                            WHERE ticker = ANY(:active))
            SELECT date FROM active GROUP BY date HAVING COUNT(*) >= 9
            ORDER BY date
        """), {"active": ACTIVE})]
        idx_pos = {d: i for i, d in enumerate(index_days)}
        worst = {}
        holes_by_t: dict[str, list] = {}
        for g in gaps:
            holes_by_t.setdefault(g.ticker, []).append(g.date)
        for tkr, dates in holes_by_t.items():
            positions = sorted(idx_pos[d] for d in dates)
            run = best = 1
            for a, b in zip(positions, positions[1:]):
                run = run + 1 if b == a + 1 else 1
                best = max(best, run)
            worst[tkr] = best
        worst_run = max(worst.values(), default=0)
        check(worst_run <= MAX_TOLERATED_RUN,
              f"no suspiciously long contiguous price gap (<= {MAX_TOLERATED_RUN} days)",
              f"longest run per ticker: {worst or 'none'}")

        # ── 2. institutional_flows ──────────────────────────────────────
        print("\n=== institutional_flows ===")
        total = c.execute(
            text("SELECT COUNT(*) FROM institutional_flows")
        ).scalar()
        print(f"  total rows: {total}")
        per_ds = c.execute(text(
            "SELECT dataset, COUNT(*) n, COUNT(DISTINCT date) d, "
            "MIN(date) lo, MAX(date) hi FROM institutional_flows "
            "GROUP BY dataset ORDER BY dataset"
        )).fetchall()
        for r in per_ds:
            print(f"  {r.dataset:18} {r.n:6} rows | {r.d:4} days | "
                  f"{r.lo} -> {r.hi}")
        for expected in ("fipi_normal", "lipi_normal",
                         "fipi_sector_wise", "lipi_sector_wise"):
            row = next((r for r in per_ds if r.dataset == expected), None)
            check(row is not None and row.lo <= WINDOW_START,
                  f"{expected} reaches the matched window start",
                  f"lo={row.lo if row else None} "
                  f"(target <= {WINDOW_START})")

        # flow days vs price trading days — the two sources should agree.
        # Reference ticker = MCB (full span, no corporate actions).
        missing = c.execute(text("""
            SELECT p.date FROM (
                SELECT DISTINCT date FROM daily_prices
                WHERE ticker = 'MCB' AND date >= :ws
            ) p
            LEFT JOIN (SELECT DISTINCT date FROM institutional_flows) f
              ON f.date = p.date
            WHERE f.date IS NULL ORDER BY p.date
        """), {"ws": WINDOW_START}).fetchall()
        check(len(missing) == 0,
              "every PSX trading day (MCB ref) has flow data",
              f"{len(missing)} missing: {[str(m.date) for m in missing[:10]]}")

        extra = c.execute(text("""
            SELECT f.date FROM (
                SELECT DISTINCT date FROM institutional_flows
            ) f
            LEFT JOIN (
                SELECT DISTINCT date FROM daily_prices WHERE ticker='MCB'
            ) p ON p.date = f.date
            WHERE p.date IS NULL ORDER BY f.date
        """)).fetchall()
        # flows on non-price days are not an error (e.g. Debt Market can
        # trade when equities are closed) — reported for the record.
        print(f"  flow days with no MCB price row: {len(extra)}"
              + (f" e.g. {[str(e.date) for e in extra[:5]]}" if extra else ""))

        # every flow date should carry all 4 datasets — catches a single
        # dataset silently failing on one day while the other three land
        partial = c.execute(text(
            "SELECT date, COUNT(DISTINCT dataset) n FROM institutional_flows "
            "GROUP BY date HAVING COUNT(DISTINCT dataset) < 4 ORDER BY date"
        )).fetchall()
        check(len(partial) == 0,
              "every flow date has all 4 datasets",
              f"{len(partial)} partial dates: "
              f"{[(str(p.date), p.n) for p in partial[:10]]}")

        # hygiene invariants (same rules as verify_institutional_flows)
        n_total = c.execute(text(
            "SELECT COUNT(*) FROM institutional_flows "
            "WHERE UPPER(market_type)='TOTAL'"
        )).scalar()
        check(n_total == 0, "no TOTAL rollup rows", f"{n_total} offenders")
        n_sep = c.execute(text(
            "SELECT COUNT(*) FROM institutional_flows WHERE sector_name='---'"
        )).scalar()
        check(n_sep == 0, "no '---' separator rows", f"{n_sep} offenders")
        n_blank = c.execute(text(
            "SELECT COUNT(*) FROM institutional_flows "
            "WHERE TRIM(client_type)=''"
        )).scalar()
        check(n_blank == 0, "no blank client_type rows", f"{n_blank} offenders")
        n_dupe = c.execute(text(
            "SELECT COUNT(*) FROM (SELECT date, dataset, client_type, "
            "sector_code, market_type, COUNT(*) FROM institutional_flows "
            "GROUP BY 1,2,3,4,5 HAVING COUNT(*) > 1) d"
        )).scalar()
        check(n_dupe == 0, "no duplicate (date,dataset,client,sector,market) rows",
              f"{n_dupe} dupe keys")

    print("\n" + "=" * 60)
    print(f"RESULT: {len(failures)} failed"
          + (f" | {failures}" if failures else ""))
    print("=" * 60)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
