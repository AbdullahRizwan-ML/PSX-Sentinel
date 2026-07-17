"""institutional_flows table (FIPI/LIPI)

Revision ID: 1f70a79ddebc
Revises: 44cd906f6e1e
Create Date: 2026-07-17 11:58:51.086318

Phase 5 Session 3 (sub-step 2). New institutional_flows table for NCCPL
FIPI/LIPI daily portfolio-investment data. The row shape was verified
against NCCPL's live internal JSON API
(POST /api/{fipi,lipi}-{normal,sector-wise}/data) on 2026-07-17; the
collector itself is deferred pending the Playwright decision (NCCPL is
behind a Cloudflare JS challenge — see docs/KNOWN_ISSUES.md).

The dedup constraint uses NULLS NOT DISTINCT (PostgreSQL 15+; live
Neon is 17.10) because sector_code is NULL for the market-wide
datasets and those rows must still deduplicate.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '1f70a79ddebc'
down_revision: Union[str, None] = '44cd906f6e1e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "institutional_flows",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("dataset", sa.String(length=30), nullable=False),
        sa.Column("client_type", sa.String(length=60), nullable=False),
        sa.Column("sector_code", sa.String(length=10), nullable=True),
        sa.Column("sector_name", sa.String(length=100), nullable=True),
        sa.Column("market_type", sa.String(length=20), nullable=False),
        sa.Column("buy_volume", sa.BigInteger(), nullable=True),
        sa.Column("buy_value", sa.Float(), nullable=True),
        sa.Column("sell_volume", sa.BigInteger(), nullable=True),
        sa.Column("sell_value", sa.Float(), nullable=True),
        sa.Column("net_volume", sa.BigInteger(), nullable=True),
        sa.Column("net_value", sa.Float(), nullable=True),
        sa.Column("usd_value", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column(
            "scraped_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.UniqueConstraint(
            "date", "dataset", "client_type", "sector_code", "market_type",
            name="uq_flow_row",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_institutional_flows_date"),
        "institutional_flows",
        ["date"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_institutional_flows_date"),
        table_name="institutional_flows",
    )
    op.drop_table("institutional_flows")
