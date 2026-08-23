#!/usr/bin/env python3
"""Copy an existing Flight Tracker AeroDataBox key into a Guest Portal secret.

This is an explicit one-shot administrative migration helper. Mount TREK
plugin-data read-only at ``/scan`` and the Guest Portal secrets directory
read/write at ``/out``. The key is written with mode 0600 and is never printed.
The long-running Guest Portal companion does not use or mount this scan path.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import sys


def read_candidate_key(path: pathlib.Path) -> str:
    """Return the provider key only when *path* matches the expected tracker schema."""
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.5) as con:
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"cache", "flights", "kv"}.issubset(tables):
                return ""
            cols = {row[1] for row in con.execute("PRAGMA table_info(cache)")}
            if not {"reservation_id", "payload", "fetched_at"}.issubset(cols):
                return ""
            row = con.execute("SELECT v FROM kv WHERE k='aerodatabox_key' LIMIT 1").fetchone()
    except (sqlite3.Error, OSError):
        return ""

    key = str(row[0]).strip() if row and row[0] is not None else ""
    return key if 0 < len(key) <= 1024 else ""


def find_keys(root: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """Find all matching databases that contain a bounded non-empty provider key."""
    matches: list[tuple[pathlib.Path, str]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name.endswith(("-wal", "-shm", ".journal")):
            continue
        key = read_candidate_key(path)
        if key:
            matches.append((path, key))
    return matches


def validate_output_path(output: pathlib.Path) -> pathlib.Path:
    """Require writes to stay below the deliberately mounted ``/out`` directory."""
    output = output.resolve()
    try:
        output.relative_to(pathlib.Path("/out").resolve())
    except ValueError as exc:
        raise ValueError("Output must be under /out") from exc
    return output


def write_secret(output: pathlib.Path, key: str) -> None:
    """Write *key* atomically enough for this one-shot workflow with mode 0600."""
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(output, 0o600)


def main(argv: list[str]) -> int:
    """Locate exactly one key, write it to the requested secret file, and report the path."""
    root = pathlib.Path(argv[1] if len(argv) > 1 else "/scan").resolve()
    try:
        output = validate_output_path(pathlib.Path(argv[2] if len(argv) > 2 else "/out/aerodatabox_api_key"))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    matches = find_keys(root)
    if not matches:
        print("Flight Tracker AeroDataBox key not found", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print("More than one Flight Tracker database with an AeroDataBox key was found; refusing to guess", file=sys.stderr)
        for path, _key in matches:
            print(f"candidate: {path}", file=sys.stderr)
        return 3

    source, key = matches[0]
    write_secret(output, key)
    print(f"AeroDataBox key extracted securely from {source} to {output}; key value was not displayed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
