"""drop advertisers table, campaigns.user_id fk to users

Revision ID: bed5a742999d
Revises: c835b6e52c74
Create Date: 2026-08-17 20:58:43.047664

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bed5a742999d'
down_revision: Union[str, Sequence[str], None] = 'c835b6e52c74'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('campaigns', sa.Column('user_id', sa.Integer(), nullable=True))

    # There's no Advertiser->User link to carry over (Advertiser predates
    # the auth build entirely, see docs/auth_plan.md) -- backfill every
    # existing campaign to an arbitrary existing user, same call as the
    # onboarding_completed/reactions migrations before it: this dev data
    # (mostly the seed catalog + pytest artifacts) has no real submitter
    # identity to recover. A fresh install has zero existing campaign
    # rows, so this is a no-op there.
    op.execute(
        "UPDATE campaigns SET user_id = (SELECT id FROM users ORDER BY id LIMIT 1) WHERE user_id IS NULL"
    )

    op.alter_column('campaigns', 'user_id', nullable=False)
    op.drop_constraint(op.f('campaigns_advertiser_id_fkey'), 'campaigns', type_='foreignkey')
    op.create_foreign_key(None, 'campaigns', 'users', ['user_id'], ['id'])
    op.drop_column('campaigns', 'advertiser_id')
    op.drop_table('advertisers')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table('advertisers',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('name', sa.VARCHAR(length=200), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('advertisers_pkey'))
    )
    op.add_column('campaigns', sa.Column('advertiser_id', sa.INTEGER(), autoincrement=False, nullable=True))
    op.drop_constraint(None, 'campaigns', type_='foreignkey')
    op.create_foreign_key(op.f('campaigns_advertiser_id_fkey'), 'campaigns', 'advertisers', ['advertiser_id'], ['id'])
    op.drop_column('campaigns', 'user_id')
