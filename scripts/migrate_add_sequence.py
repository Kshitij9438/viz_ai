#!/usr/bin/env python3
"""
One-shot migration: add `sequence` column to the `messages` table.

Run from the backend/ directory:
    python scripts/migrate_add_sequence.py

Safe to run multiple times (checks for column existence first).
After running, existing rows will have sequence=0. They are ordered by
SQLite's implicit rowid (insertion order), which is correct for history
replay. New rows will be assigned proper monotonic sequence values by
the application.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "backend" / "vizzy.db"


def main() -> None:
    if not DB_PATH.exists():
        print(f"[migrate] Database not found at {DB_PATH}. Nothing to do.")
        sys.exit(0)

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Check if column already exists
    cur.execute("PRAGMA table_info(messages)")
    columns = [row[1] for row in cur.fetchall()]

    if "sequence" in columns:
        print("[migrate] Column 'sequence' already exists in messages. Skipping.")
        con.close()
        return

    print("[migrate] Adding 'sequence' column to messages table...")
    cur.execute("ALTER TABLE messages ADD COLUMN sequence INTEGER NOT NULL DEFAULT 0")

    # Back-fill existing rows: assign each row its rowid as sequence so that
    # the existing data is replay-ordered correctly (rowid == insertion order in SQLite).
    cur.execute("UPDATE messages SET sequence = rowid WHERE sequence = 0")

    con.commit()
    con.close()
    print(f"[migrate] Done. Existing rows back-filled with their rowid as sequence.")


if __name__ == "__main__":
    main()
