#!/usr/bin/env python3
"""One-shot extractor for Flight Tracker's AeroDataBox key.

Mount TREK plugins-data read-only at /scan and a dedicated Guest Portal secrets
folder read/write at /out. The key is written to the requested output file and
is never printed to stdout/stderr.
"""
import os
import pathlib
import sqlite3
import sys

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/scan").resolve()
out = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "/out/aerodatabox_api_key").resolve()

try:
    out.relative_to(pathlib.Path("/out").resolve())
except ValueError:
    print("Output must be under /out", file=sys.stderr)
    raise SystemExit(2)

matches = []
for p in root.rglob("*"):
    if not p.is_file() or p.name.endswith(("-wal", "-shm", ".journal")):
        continue
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=0.5)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"cache", "flights", "kv"}.issubset(tables):
                continue
            cols = {r[1] for r in con.execute("PRAGMA table_info(cache)")}
            if not {"reservation_id", "payload", "fetched_at"}.issubset(cols):
                continue
            row = con.execute("SELECT v FROM kv WHERE k='aerodatabox_key' LIMIT 1").fetchone()
            if row and row[0] is not None:
                key = str(row[0]).strip()
                if key and len(key) <= 1024:
                    matches.append((p, key))
        finally:
            con.close()
    except Exception:
        pass

if not matches:
    print("Flight Tracker AeroDataBox key not found", file=sys.stderr)
    raise SystemExit(1)
if len(matches) > 1:
    print("More than one Flight Tracker database with an AeroDataBox key was found; refusing to guess", file=sys.stderr)
    for p, _ in matches:
        print(f"candidate: {p}", file=sys.stderr)
    raise SystemExit(3)

src, key = matches[0]
out.parent.mkdir(parents=True, exist_ok=True)
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
try:
    os.write(fd, key.encode("utf-8"))
finally:
    os.close(fd)
os.chmod(out, 0o600)
print(f"AeroDataBox key extracted securely from {src} to {out}; key value was not displayed")
