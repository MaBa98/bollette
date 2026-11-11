"""Catalogo offerte reale (open data Portale Offerte), snapshot giornaliero, regione utente.

Revision ID: 0004_real_market
Revises: 0003_supply_cost
Create Date: 2026-11-11

La vecchia market_offers conteneva solo dati mock: viene eliminata e ricreata con il nuovo schema.
SQL esplicito e idempotente (IF EXISTS / IF NOT EXISTS) perché la 0001 usa create_all sui modelli
correnti: su un database nuovo queste tabelle esistono già quando arriva questa migrazione.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_real_market"
down_revision = "0003_supply_cost"
branch_labels = None
depends_on = None


def _is_supabase() -> bool:
    return bool(op.get_bind().execute(sa.text("SELECT to_regprocedure('auth.uid()') IS NOT NULL")).scalar())


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS market_offers")
    op.execute("DROP TYPE IF EXISTS offer_price_type")
    op.execute(
        """
        CREATE TABLE market_offers (
            id UUID PRIMARY KEY,
            published_on DATE NOT NULL,
            source VARCHAR(20) NOT NULL,
            offer_code VARCHAR(64) NOT NULL,
            piva VARCHAR(16),
            supplier_name VARCHAR(200),
            offer_name VARCHAR(300),
            commodity VARCHAR(16) NOT NULL,
            client_type VARCHAR(2),
            price_kind VARCHAR(16),
            valid_to TIMESTAMP WITHOUT TIME ZONE,
            data JSONB NOT NULL,
            CONSTRAINT uq_offer_publication UNIQUE (published_on, source, offer_code, piva)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_offer_lookup ON market_offers (published_on, commodity, client_type)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS market_snapshots (
            published_on DATE PRIMARY KEY,
            parameters_electricity JSONB NOT NULL,
            parameters_gas JSONB NOT NULL,
            index_values JSONB NOT NULL,
            offers_electricity INTEGER NOT NULL DEFAULT 0,
            offers_gas INTEGER NOT NULL DEFAULT 0,
            ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS region_code VARCHAR(2)")

    if _is_supabase():
        for table in ("market_offers", "market_snapshots"):
            op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"DROP POLICY IF EXISTS {table}_read ON public.{table}")
            op.execute(f"CREATE POLICY {table}_read ON public.{table} FOR SELECT TO authenticated USING (true)")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS region_code")
    op.execute("DROP TABLE IF EXISTS market_snapshots")
    op.execute("DROP TABLE IF EXISTS market_offers")
