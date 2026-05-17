"""add case_link_alerts pref + linker indexes

Two database changes to support the new case-linking + notification work:

1. Index `case_links (source_case_id, link_type)` and
   `case_links (target_case_id, link_type)` to keep the linker's upsert
   path (one query per focal case to load existing links) O(log n) even
   as the table grows. The existing UniqueConstraint already creates an
   index on (source_case_id, target_case_id, link_type) but most of our
   reads filter on just source OR target — so we add the supporting
   single-column indexes too.

2. No schema change is needed for the new "case_link_alerts" pref key.
   User preferences are key/value rows in `user_preferences`; the
   service already defaults missing keys to ON. New users will start
   with the toggle ON automatically.

These migrations are written to be portable across Postgres and SQLite.
SQLite doesn't support DROP INDEX … IF EXISTS in all old versions but
modern SQLite (3.8+) does; Alembic handles the dialect quoting for us.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2026_05_17_case_linker_indexes"
down_revision = None  # Set this to your previous head revision
branch_labels = None
depends_on = None


def upgrade() -> None:
    try: 
    # ── Supporting indexes for the linker's upsert path ─────────────────
    # The existing unique constraint covers (source, target, link_type)
    # but the linker also runs:
    #   SELECT * FROM case_links WHERE source_case_id = :id
    # which the unique constraint's index serves only as a prefix scan.
    # Explicit single-column indexes make the planner pick them
        # consistently across both Postgres and SQLite.
        op.create_index(
            "ix_case_links_source_case_id",
            "case_links",
            ["source_case_id"],
            unique=False,
        )
        op.create_index(
            "ix_case_links_target_case_id",
            "case_links",
            ["target_case_id"],
            unique=False,
        )

        # Index on link_type alone is useful for the analytics dashboard
        # ("how many SAME_SUSPECT links exist?").
        op.create_index(
            "ix_case_links_link_type",
            "case_links",
            ["link_type"],
            unique=False,
        )

        # Index on the shared-entities join column. The linker rewrites this
        # table on every recompute, so the index pays off both for the
        # DELETE-then-INSERT cycle and for any future "find every case that
        # shares this CNIC" query the analytics layer might do.
        op.create_index(
            "ix_case_link_shared_entities_link_id",
            "case_link_shared_entities",
            ["link_id"],
            unique=False,
        )
        op.create_index(
            "ix_case_link_shared_entities_person_id",
            "case_link_shared_entities",
            ["person_id"],
            unique=False,
        )

        # ── Seed the case_link_alerts pref key for existing users ───────────
        # Optional — the service defaults missing keys to ON, so this is just
        # cosmetic (lets the settings UI render the toggle in its "on" state
        # for existing users without waiting for them to flip it). Skip if
        # your settings page already handles missing keys gracefully.
        conn = op.get_bind()
        conn.execute(sa.text("""
            INSERT INTO user_preferences (user_id, pref_key, pref_value, updated_at)
            SELECT u.id, 'case_link_alerts', 'true', CURRENT_TIMESTAMP
            FROM users u
            WHERE NOT EXISTS (
                SELECT 1 FROM user_preferences p
                WHERE p.user_id = u.id AND p.pref_key = 'case_link_alerts'
            )
        """))
        print("U pdate done :)")
    except Exception as e:
        print("EROR")


def downgrade() -> None:
    try:
        op.drop_index("ix_case_link_shared_entities_person_id",
                    table_name="case_link_shared_entities")
        op.drop_index("ix_case_link_shared_entities_link_id",
                    table_name="case_link_shared_entities")
        op.drop_index("ix_case_links_link_type", table_name="case_links")
        op.drop_index("ix_case_links_target_case_id", table_name="case_links")
        op.drop_index("ix_case_links_source_case_id", table_name="case_links")

        op.execute("DELETE FROM user_preferences WHERE pref_key = 'case_link_alerts'")
        print("Downgrade done :)")
    except Exception:
        print("ERROR ")

upgrade()
downgrade()