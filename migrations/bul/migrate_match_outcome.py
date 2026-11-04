"""
Safe migration runner — adds outcome_type and outcome_details to the match table
if they do not already exist. Works for both SQLite (dev) and MySQL (prod).

Usage:
    python migrations/bul/migrate_match_outcome.py
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from app import app
from models import db
from sqlalchemy import text, inspect as sa_inspect


def column_exists(engine, table, column):
    inspector = sa_inspect(engine)
    return any(c['name'] == column for c in inspector.get_columns(table))


def run_migration():
    with app.app_context():
        engine = db.engine
        is_sqlite = engine.dialect.name == 'sqlite'

        with engine.begin() as conn:
            if not column_exists(engine, 'match', 'outcome_type'):
                print("Adding column: outcome_type ...")
                if is_sqlite:
                    conn.execute(text(
                        "ALTER TABLE `match` ADD COLUMN outcome_type VARCHAR(20) NOT NULL DEFAULT 'regular'"
                    ))
                else:
                    conn.execute(text(
                        "ALTER TABLE `match` ADD COLUMN outcome_type VARCHAR(20) NOT NULL DEFAULT 'regular'"
                    ))
                print("  [OK] outcome_type added")
            else:
                print("  [SKIP] outcome_type already exists — skipping")

            if not column_exists(engine, 'match', 'outcome_details'):
                print("Adding column: outcome_details ...")
                conn.execute(text(
                    "ALTER TABLE `match` ADD COLUMN outcome_details TEXT NULL"
                ))
                print("  [OK] outcome_details added")
            else:
                print("  [SKIP] outcome_details already exists — skipping")

        print("\nMigration complete!")


if __name__ == '__main__':
    run_migration()
