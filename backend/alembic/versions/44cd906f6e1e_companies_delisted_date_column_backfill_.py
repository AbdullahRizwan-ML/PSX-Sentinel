"""companies.delisted_date column, backfill ENGRO

Revision ID: 44cd906f6e1e
Revises: 93dd6a13c006
Create Date: 2026-07-17 11:34:54.440476

Phase 5 Session 3 (sub-step 1). Hand-written delta, same pattern as
93dd6a13c006:

1. New nullable companies.delisted_date (Date). NULL = still listed.
2. Backfill ENGRO's delisted_date = 2025-01-14 — the PSX formal
   delisting effective date (Scheme of Arrangement: Engro Corporation
   merged into Dawood Hercules, renamed Engro Holdings / ENGROH; last
   trading day 2025-01-13). Confirmed live 2026-07-17: PSX DPS still
   serves ENGRO price data but the series ends 2025-01-03, matching our
   daily_prices exactly; ENGROH serves live data through the present.
   See docs/KNOWN_ISSUES.md ("ENGRO stopped trading in Jan 2025").
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '44cd906f6e1e'
down_revision: Union[str, None] = '93dd6a13c006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("delisted_date", sa.Date(), nullable=True),
    )
    op.execute(
        "UPDATE companies SET delisted_date = DATE '2025-01-14' "
        "WHERE ticker = 'ENGRO'"
    )


def downgrade() -> None:
    op.drop_column("companies", "delisted_date")
