#!/usr/bin/env python3
import pathlib, sqlite3, sys

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else '/scan')
found = []
for p in root.rglob('*'):
    if not p.is_file() or p.name.endswith(('-wal','-shm','.journal')):
        continue
    try:
        con = sqlite3.connect(f'file:{p}?mode=ro', uri=True, timeout=0.5)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if {'cache','flights','kv'}.issubset(tables):
            cols = {r[1] for r in con.execute('PRAGMA table_info(cache)')}
            if {'reservation_id','payload','fetched_at'}.issubset(cols):
                mode = con.execute('PRAGMA journal_mode').fetchone()[0]
                found.append((p, mode))
        con.close()
    except Exception:
        pass
if not found:
    print('Flight Tracker DB not found', file=sys.stderr)
    raise SystemExit(1)
for p, mode in found:
    print(f'{p}	journal_mode={mode}	wal_present={(pathlib.Path(str(p)+"-wal")).exists()}')
