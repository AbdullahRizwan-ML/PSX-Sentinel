"""
PSX Sentinel — Institutional Flows Verification (Phase 5 Session 4)

Read-only. Verifies the NCCPL FIPI/LIPI data sitting in
institutional_flows via DIRECT SQL on a fresh sync psycopg2 connection:
row counts per dataset, date coverage, distinct client types + market
types, sector coverage, a net-flow sanity spot-check, and confirmation
that no TOTAL rollup rows leaked in.

    python scripts/verify_institutional_flows.py

NOTE: this verifies the DATA. How the rows got there (browser-pane fetch
+ bulk load, because automated Playwright can't pass NCCPL's Cloudflare
challenge on this machine) is documented in the Build Log and the
institutional_flow_collector docstring — not re-litigated here.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = "[PASS]", "[FAIL]"
failures: list[str] = []


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
        total = c.execute(
            text("SELECT COUNT(*) FROM institutional_flows")
        ).scalar()
        print(f"\n=== institutional_flows: {total} rows (direct SQL) ===")
        check(total > 0, "table is populated", f"{total} rows")

        print("\nper dataset (rows | dates | date range):")
        per_ds = c.execute(text(
            "SELECT dataset, COUNT(*) n, COUNT(DISTINCT date) d, "
            "MIN(date) lo, MAX(date) hi FROM institutional_flows "
            "GROUP BY dataset ORDER BY dataset"
        )).fetchall()
        seen = set()
        for r in per_ds:
            print(f"  {r.dataset:18} {r.n:5} | {r.d:2} days | {r.lo} -> {r.hi}")
            seen.add(r.dataset)
        for expected in (
            "fipi_normal", "lipi_normal",
            "fipi_sector_wise", "lipi_sector_wise",
        ):
            check(expected in seen, f"dataset present: {expected}")

        print("\ndistinct client types:")
        for r in c.execute(text(
            "SELECT dataset, client_type, COUNT(*) n FROM institutional_flows "
            "GROUP BY dataset, client_type ORDER BY dataset, client_type"
        )):
            print(f"  {r.dataset:18} {r.client_type:22} {r.n}")

        markets = [r.market_type for r in c.execute(text(
            "SELECT DISTINCT market_type FROM institutional_flows "
            "ORDER BY market_type"
        ))]
        print("\ndistinct market types:", markets)
        check(
            "TOTAL" not in markets,
            "no TOTAL rollup rows stored",
            f"got {markets}",
        )

        sectors = [r.sector_name for r in c.execute(text(
            "SELECT DISTINCT sector_name FROM institutional_flows "
            "WHERE sector_name IS NOT NULL ORDER BY sector_name"
        ))]
        print(f"\ndistinct sectors ({len(sectors)}):")
        for s in sectors:
            print(f"  {s}")
        check(len(sectors) >= 5, "sector-wise data present", f"{len(sectors)} sectors")
        check(
            "---" not in sectors,
            "no separator ('---') sector rows stored",
        )

        # market-wide rows must have NULL sector; sector rows must not
        bad_normal = c.execute(text(
            "SELECT COUNT(*) FROM institutional_flows "
            "WHERE dataset LIKE '%_normal' AND sector_code IS NOT NULL"
        )).scalar()
        check(bad_normal == 0, "market-wide rows have NULL sector_code",
              f"{bad_normal} offenders")

        # net_value plausibility: a known non-null spot value
        nn = c.execute(text(
            "SELECT COUNT(*) FROM institutional_flows WHERE net_value IS NULL"
        )).scalar()
        check(nn == 0, "no NULL net_value", f"{nn} nulls")

        print("\nnet-flow sanity — foreign net (fipi_normal, REG) latest day:")
        for r in c.execute(text(
            "SELECT date, client_type, net_value FROM institutional_flows "
            "WHERE dataset='fipi_normal' AND market_type='REG' "
            "AND date=(SELECT MAX(date) FROM institutional_flows "
            "          WHERE dataset='fipi_normal') "
            "ORDER BY client_type"
        )):
            print(f"  {r.date} {r.client_type:22} net={r.net_value:,.0f} PKR")

    print("\n" + "=" * 60)
    print(f"RESULT: {len(failures)} failed"
          + (f" | {failures}" if failures else ""))
    print("=" * 60)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
