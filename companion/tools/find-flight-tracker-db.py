#!/usr/bin/env python3
"""Locate candidate Flight Tracker SQLite databases for one-shot key migration.

This helper is never invoked by the public Guest Portal container. An
administrator may run it manually against a read-only TREK plugin-data mount to
identify which database can be passed to the key-extraction workflow.
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys


def looks_like_tracker_database(path: pathlib.Path) -> tuple[bool, str]:
    """Return whether *path* has the expected Flight Tracker schema and journal mode."""
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.5) as con:
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"cache", "flights", "kv"}.issubset(tables):
                return False, ""
            cols = {row[1] for row in con.execute("PRAGMA table_info(cache)")}
            if not {"reservation_id", "payload", "fetched_at"}.issubset(cols):
                return False, ""
            mode = str(con.execute("PRAGMA journal_mode").fetchone()[0])
            return True, mode
    except (sqlite3.Error, OSError):
        return False, ""


def find_candidates(root: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """Scan regular files below *root* and return databases matching the known schema."""
    found: list[tuple[pathlib.Path, str]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name.endswith(("-wal", "-shm", ".journal")):
            continue
        matched, mode = looks_like_tracker_database(path)
        if matched:
            found.append((path, mode))
    return found


def main(argv: list[str]) -> int:
    """Print candidate paths without reading or displaying provider secret values."""
    root = pathlib.Path(argv[1] if len(argv) > 1 else "/scan").resolve()
    found = find_candidates(root)
    if not found:
        print("Flight Tracker DB not found", file=sys.stderr)
        return 1
    for path, mode in found:
        wal_present = pathlib.Path(f"{path}-wal").exists()
        print(f"{path}\tjournal_mode={mode}\twal_present={wal_present}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
