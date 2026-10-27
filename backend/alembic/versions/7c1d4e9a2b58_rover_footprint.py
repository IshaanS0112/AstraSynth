"""rover footprint and clearance radius; traverse executive record"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c1d4e9a2b58"
down_revision: str | None = "30be9b387be3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Server defaults so existing rows become point rovers, which plan exactly as
    # they did before this column existed.
    op.add_column(
        "rover_configs",
        sa.Column("footprint_radius_m", sa.Float(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "rover_configs",
        sa.Column("clearance_radius_m", sa.Float(), server_default=sa.text("0"), nullable=False),
    )
    # Nullable: a run recorded before the executive existed has none.
    op.add_column(
        "traverse_runs",
        sa.Column("executive", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("traverse_runs", "executive")
    op.drop_column("rover_configs", "clearance_radius_m")
    op.drop_column("rover_configs", "footprint_radius_m")
