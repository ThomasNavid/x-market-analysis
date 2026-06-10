"""Minimal raw-SQL migration runner.

This is deliberately small so the migration mechanics are easy to inspect:
read sorted `.sql` files, skip versions already recorded in schema_migrations,
execute the rest in transactions, and record what ran.
"""

from dataclasses import dataclass
from pathlib import Path

from findb.core.db.connection import connect
from findb.core.paths import find_project_root

PROJECT_ROOT = find_project_root()
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path


def discover_migrations() -> list[Migration]:
    """Return SQL migration files in filename order."""
    return [
        Migration(version=path.stem, path=path) for path in sorted(MIGRATIONS_DIR.glob("*.sql"))
    ]


def ensure_history_table() -> None:
    """Create the migration history table if it does not exist."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
        conn.commit()


def applied_versions() -> set[str]:
    """Read migration versions already applied to this database."""
    ensure_history_table()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations ORDER BY version")
            return {row[0] for row in cur.fetchall()}


def pending_migrations() -> list[Migration]:
    """Return migrations that have not been applied yet."""
    applied = applied_versions()
    return [migration for migration in discover_migrations() if migration.version not in applied]


def apply_pending_migrations() -> list[Migration]:
    """Apply pending SQL migrations and return the migrations that ran."""
    ensure_history_table()
    ran: list[Migration] = []

    with connect() as conn:
        with conn.cursor() as cur:
            for migration in pending_migrations():
                sql = migration.path.read_text(encoding="utf-8")
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (migration.version,),
                )
                ran.append(migration)
        conn.commit()

    return ran
