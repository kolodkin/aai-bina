"""connections: per-driver columns -> one encrypted JSON config blob

Revision ID: a1b2c3d4e5f6
Revises: 9a536b7c0328
Create Date: 2026-06-20

"""
from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "9a536b7c0328"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add the new blob column (nullable while we backfill).
    with op.batch_alter_table("connections", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("config", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )

    # 2. Backfill each existing row: decrypt the old password, build the config
    #    dict, re-encrypt the whole JSON. The app's key loader is imported here.
    from queryview.connect import _decrypt_str, _encrypt_str

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, host, port, username, password FROM connections")
    ).fetchall()
    for rid, host, port, username, password in rows:
        plain = _decrypt_str(password) if password else ""
        blob = _encrypt_str(
            json.dumps(
                {"host": host, "port": port, "username": username, "password": plain}
            )
        )
        conn.execute(
            sa.text("UPDATE connections SET config = :c WHERE id = :i"),
            {"c": blob, "i": rid},
        )

    # 3. Enforce NOT NULL and drop the per-driver columns.
    with op.batch_alter_table("connections", schema=None) as batch_op:
        batch_op.alter_column(
            "config", existing_type=sqlmodel.sql.sqltypes.AutoString(), nullable=False
        )
        batch_op.drop_column("host")
        batch_op.drop_column("port")
        batch_op.drop_column("username")
        batch_op.drop_column("password")


def downgrade() -> None:
    with op.batch_alter_table("connections", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("host", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.add_column(sa.Column("port", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("username", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("password", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.drop_column("config")
