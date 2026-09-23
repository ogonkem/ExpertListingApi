"""create listings

Revision ID: 924634b743a2
Revises:
Create Date: 2026-09-23 16:06:02.957135

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography

# revision identifiers, used by Alembic.
revision: str = "924634b743a2"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "listings",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), server_default=sa.text("'NGN'"), nullable=False),
        sa.Column(
            "listing_type",
            sa.Enum("rent", "sale", "shortlet", name="listing_type"),
            nullable=False,
        ),
        sa.Column("bedrooms", sa.SmallInteger(), nullable=False),
        sa.Column(
            "location",
            Geography(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("address", sa.String(length=300), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("bedrooms >= 0", name=op.f("ck_listings_bedrooms_non_negative")),
        sa.CheckConstraint("price > 0", name=op.f("ck_listings_price_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_listings")),
    )
    op.create_index(op.f("ix_listings_agent_id"), "listings", ["agent_id"], unique=False)
    op.create_index(
        "ix_listings_location", "listings", ["location"], unique=False, postgresql_using="gist"
    )
    op.create_index(
        "ix_listings_type_price_bedrooms",
        "listings",
        ["listing_type", "price", "bedrooms"],
        unique=False,
    )

    # Keep updated_at current on every UPDATE, whichever client issues it.
    op.execute(
        """
        CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER listings_set_updated_at
        BEFORE UPDATE ON listings
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS listings_set_updated_at ON listings")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
    op.drop_index("ix_listings_type_price_bedrooms", table_name="listings")
    op.drop_index("ix_listings_location", table_name="listings", postgresql_using="gist")
    op.drop_index(op.f("ix_listings_agent_id"), table_name="listings")
    op.drop_table("listings")
    op.execute("DROP TYPE IF EXISTS listing_type")
    # The postgis extension is intentionally left installed: other objects (and the
    # image's topology/tiger extensions) depend on it.
