"""Schema iniziale + Row Level Security (se il DB è Supabase).

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-11

Eccezione voluta alla buona pratica: questa prima migrazione usa i modelli correnti
(`create_all`) invece di un elenco di op.create_table congelato. Dalla prossima in poi si usa
`alembic revision --autogenerate -m "..."`, che produce operazioni esplicite.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.models import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

_TENANT_TABLES = {"users": "id", "invoices": "user_id", "comparison_tasks": "user_id"}
_ENUM_TYPES = ("provider", "commodity", "consumption_unit", "source_kind", "task_status")


def _is_supabase(bind: sa.Connection) -> bool:
    """auth.uid() esiste solo su Supabase: su un Postgres qualsiasi la RLS viene saltata."""
    return bool(
        bind.execute(
            sa.text("SELECT to_regprocedure('auth.uid()') IS NOT NULL")
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)

    if not _is_supabase(bind):
        return

    # Difesa in profondità: blocca l'accesso diretto via PostgREST con la anon key.
    # Il backend usa il ruolo owner, che bypassa la RLS: il filtro per tenant resta nei repository.
    for table, owner_column in _TENANT_TABLES.items():
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_owner_read ON public.{table}")
        op.execute(
            f"CREATE POLICY {table}_owner_read ON public.{table} "
            f"FOR SELECT TO authenticated USING ({owner_column} = auth.uid())"
        )

    op.execute("ALTER TABLE public.market_offers ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS market_offers_read ON public.market_offers")
    op.execute(
        "CREATE POLICY market_offers_read ON public.market_offers "
        "FOR SELECT TO authenticated USING (true)"
    )
    # Nessuna policy di scrittura: dal client INSERT/UPDATE/DELETE sono negate.


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind)
    for enum_name in _ENUM_TYPES:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
