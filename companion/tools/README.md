# Helper tools

## `find-flight-tracker-db.py`

Scans a directory you explicitly mount and reports candidate Flight Tracker SQLite databases without printing API keys.

## `extract-flight-tracker-key.py`

One-shot migration convenience. It looks for `kv(k='aerodatabox_key')` in a Flight Tracker SQLite database and writes the value to a requested output file without printing the key.

It **cannot** recover a key that exists only in TREK's encrypted Admin plugin configuration. In that case create `secrets/aerodatabox_api_key` manually.

These tools are not used by the long-running hardened companion container.
