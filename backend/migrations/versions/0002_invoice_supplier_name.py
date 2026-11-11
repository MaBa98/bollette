"""Nome commerciale del fornitore sulla bolletta.

Revision ID: 0002_supplier_name
Revises: 0001_initial
Create Date: 2026-11-11

IF NOT EXISTS è necessario: la 0001 usa create_all sui modelli correnti, quindi su un database
nuovo la colonna esiste già quando arriva questa migrazione.
"""

from __future__ import annotations

from alembic import op

revision = "0002_supplier_name"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE invoices ADD COLUMN IF NOT EXISTS supplier_name VARCHAR(120)")


def downgrade() -> None:
    op.execute("ALTER TABLE invoices DROP COLUMN IF EXISTS supplier_name")
