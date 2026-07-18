"""intelligence_reports fundamentals/flow contribution columns

Revision ID: 1575e060ba63
Revises: 1f70a79ddebc
Create Date: 2026-07-18 20:46:38.250232

Phase 5 Session 8. Hand-written delta, same pattern as the previous
three migrations.

Adds two nullable Float columns to intelligence_reports:

1. fundamentals_contribution — the deterministic value/quality tilt
   (peer-percentile rank of P/E and dividend yield vs the active
   universe, clamped to +-10) the Arbitrator added to the conviction
   score for this report.
2. flow_contribution — the deterministic sector-level FIPI/LIPI
   institutional-flow regime term (10-trading-day imbalance ratio of
   foreign + local-institutional net turnover in the ticker's mapped
   NCCPL sector(s), staleness-gated, clamped to +-10).

Both are NULLABLE on purpose: rows written before this session carry
NULL ("the term did not exist when this report was generated"), which
is distinguishable from a computed 0.0 ("the term ran and contributed
nothing") — the same honest-zero discipline as filing_contribution.

Note these are the FIRST first-class per-term columns on
intelligence_reports. The four legacy terms (technical/news/filing/ml)
live only inside agent_outputs['arbitrator']['output']['score_breakdown']
(JSON) and are hoisted into the API response by a Pydantic validator —
that stays unchanged. The two new terms are ALSO present in that same
score_breakdown JSON so the API/frontend path stays uniform; the
columns exist on top for direct-SQL auditability of the new
deterministic signals.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1575e060ba63'
down_revision: Union[str, None] = '1f70a79ddebc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "intelligence_reports",
        sa.Column("fundamentals_contribution", sa.Float(), nullable=True),
    )
    op.add_column(
        "intelligence_reports",
        sa.Column("flow_contribution", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("intelligence_reports", "flow_contribution")
    op.drop_column("intelligence_reports", "fundamentals_contribution")
