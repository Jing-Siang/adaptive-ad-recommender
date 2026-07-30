"""set campaigns replica identity full for logical replication

Revision ID: bf2edd83f9b3
Revises: 130bff812dd0
Create Date: 2026-07-30 15:48:09.350832

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bf2edd83f9b3'
down_revision: Union[str, Sequence[str], None] = '130bff812dd0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Postgres's default REPLICA IDENTITY only includes primary-key columns
    in a logical-replication UPDATE's "before" image -- non-key columns
    (status, budget_spent, ...) would show up as null in
    payload.before, even though they obviously had prior values. Needed so
    the Kafka CDC consumer (see docs/kafka_cdc_plan.md, not built yet) can
    diff old-vs-new eligibility state instead of only ever seeing the new
    row. DB-only change, no application code depends on this yet.
    """
    op.execute("ALTER TABLE campaigns REPLICA IDENTITY FULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE campaigns REPLICA IDENTITY DEFAULT")
