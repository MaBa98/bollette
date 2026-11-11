"""Quota materia energia (IVA esclusa) sulla bolletta, base del calcolo del risparmio.

Revision ID: 0003_supply_cost
Revises: 0002_supplier_name
Create Date: 2026-11-11

IF NOT EXISTS: la 0001 usa create_all sui modelli correnti (vedi nota nella 0002).
"""

from __future__ import annotations

from alembic import op

revision = "0003_supply_cost"
down_revision = "0002_supplier_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS supply_cost_eur NUMERIC(10, 2)")


def downgrade() -> None:
    op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS supply_cost_eur")
