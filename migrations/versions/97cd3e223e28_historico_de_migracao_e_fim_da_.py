"""historico de migracao e fim da duplicidade

Revision ID: 97cd3e223e28
Revises: 8783a79d6c11
Create Date: 2026-10-10 19:36:30.046944

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '97cd3e223e28'
down_revision = '8783a79d6c11'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("migration_count", sa.Integer(), server_default="0", nullable=False))
        batch_op.add_column(sa.Column("migrated_at", sa.DateTime(), nullable=True))

    # A migração antiga marcava a entrada como situação "migrated" e criava uma
    # CÓPIA para o novo dia — isso gerava duplicidade. Agora migrar só muda a
    # data, então os registros antigos voltam a ser pendências normais, já na
    # data para onde tinham rolado (o histórico vai para migration_count).
    # As cópias geradas por aquele comportamento são removidas por
    # `flask dedupe-tasks` (explícito, com backup antes).
    op.execute(
        """
        UPDATE tasks
           SET status = 'open',
               scheduled_date = COALESCE(migrated_to_date, scheduled_date),
               migration_count = migration_count + 1,
               migrated_at = COALESCE(migrated_at, updated_at)
         WHERE status = 'migrated'
        """
    )


def downgrade():
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("migrated_at")
        batch_op.drop_column("migration_count")
