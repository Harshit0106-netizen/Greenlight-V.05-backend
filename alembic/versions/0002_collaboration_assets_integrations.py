"""collaboration, image assets and integrations

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-10 09:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_asset",
        sa.Column("id", sa.String(length=20), nullable=False),
        sa.Column("review_id", sa.String(length=20), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("content_type", sa.String(length=40), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("alt_text", sa.Text(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("palette_checked", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["review_id"], ["review.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_review_asset_review_id"), "review_asset", ["review_id"], unique=False)

    op.create_table(
        "api_key",
        sa.Column("id", sa.String(length=20), nullable=False),
        sa.Column("workspace_id", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("submit_as_user_id", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.ForeignKeyConstraint(["submit_as_user_id"], ["app_user.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_key_workspace_id"), "api_key", ["workspace_id"], unique=False)
    op.create_index(op.f("ix_api_key_key_hash"), "api_key", ["key_hash"], unique=True)

    op.create_table(
        "webhook",
        sa.Column("id", sa.String(length=20), nullable=False),
        sa.Column("workspace_id", sa.String(length=20), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("secret", sa.String(length=80), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_webhook_workspace_id"), "webhook", ["workspace_id"], unique=False)

    op.create_table(
        "webhook_delivery",
        sa.Column("pk", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.String(length=20), nullable=False),
        sa.Column("webhook_id", sa.String(length=20), nullable=False),
        sa.Column("review_id", sa.String(length=20), nullable=True),
        sa.Column("event", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.ForeignKeyConstraint(["webhook_id"], ["webhook.id"]),
        sa.ForeignKeyConstraint(["review_id"], ["review.id"]),
        sa.PrimaryKeyConstraint("pk"),
    )
    op.create_index(op.f("ix_webhook_delivery_workspace_id"), "webhook_delivery", ["workspace_id"], unique=False)
    op.create_index(op.f("ix_webhook_delivery_webhook_id"), "webhook_delivery", ["webhook_id"], unique=False)

    # batch mode keeps this working on SQLite as well as PostgreSQL
    with op.batch_alter_table("review") as batch:
        batch.add_column(sa.Column("assignee_id", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("escalated_by", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("escalation_note", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("structured", sa.JSON(), nullable=True))
        batch.create_foreign_key("fk_review_assignee_id", "app_user", ["assignee_id"], ["id"])
        batch.create_foreign_key("fk_review_escalated_by", "app_user", ["escalated_by"], ["id"])
    with op.batch_alter_table("finding") as batch:
        batch.add_column(sa.Column("asset_id", sa.String(length=20), nullable=True))
        batch.create_index(op.f("ix_finding_asset_id"), ["asset_id"], unique=False)
        batch.create_foreign_key("fk_finding_asset_id", "review_asset", ["asset_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("finding") as batch:
        batch.drop_constraint("fk_finding_asset_id", type_="foreignkey")
        batch.drop_index(op.f("ix_finding_asset_id"))
        batch.drop_column("asset_id")
    with op.batch_alter_table("review") as batch:
        batch.drop_constraint("fk_review_escalated_by", type_="foreignkey")
        batch.drop_constraint("fk_review_assignee_id", type_="foreignkey")
        for col in ("structured", "escalation_note", "escalated_by", "escalated_at", "assigned_at", "assignee_id"):
            batch.drop_column(col)
    op.drop_index(op.f("ix_webhook_delivery_webhook_id"), table_name="webhook_delivery")
    op.drop_index(op.f("ix_webhook_delivery_workspace_id"), table_name="webhook_delivery")
    op.drop_table("webhook_delivery")
    op.drop_index(op.f("ix_webhook_workspace_id"), table_name="webhook")
    op.drop_table("webhook")
    op.drop_index(op.f("ix_api_key_key_hash"), table_name="api_key")
    op.drop_index(op.f("ix_api_key_workspace_id"), table_name="api_key")
    op.drop_table("api_key")
    op.drop_index(op.f("ix_review_asset_review_id"), table_name="review_asset")
    op.drop_table("review_asset")
