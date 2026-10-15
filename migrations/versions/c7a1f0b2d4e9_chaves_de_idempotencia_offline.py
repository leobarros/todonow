"""chaves de idempotencia para as acoes reenviadas do modo offline

Revision ID: c7a1f0b2d4e9
Revises: 97cd3e223e28
Create Date: 2026-10-12 20:36:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c7a1f0b2d4e9'
down_revision = '97cd3e223e28'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "client_operations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("endpoint", sa.String(length=64), nullable=False),
        sa.Column("status_code", sa.Integer(), server_default="200", nullable=False),
        sa.Column("response_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "key", "endpoint", name="uq_client_operations_user_key"),
    )
    with op.batch_alter_table("client_operations", schema=None) as batch_op:
        batch_op.create_index("ix_client_operations_created", ["created_at"], unique=False)


def downgrade():
    with op.batch_alter_table("client_operations", schema=None) as batch_op:
        batch_op.drop_index("ix_client_operations_created")
    op.drop_table("client_operations")
