#!/usr/bin/env python3
"""
Add keys that are missing from the server .env, taking values from another .env.

* Never overwrites a key the server already has.
* Never copies development-only values (DEBUG=true, Windows paths, localhost
  hosts for public URLs) - those are listed so you can set them by hand.
* Writes a timestamped backup of the server .env first.

    python deploy/merge_env.py --server .env --source /path/to/local.env           # preview
    python deploy/merge_env.py --server .env --source /path/to/local.env --write   # apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
from pathlib import Path

DEV_ONLY_KEYS = {"DJANGO_DEBUG", "DJANGO_ALLOW_INSECURE_DEV_SECRET", "DJANGO_USE_SQLITE", "DJANGO_TASKS_RUN_INLINE",
                 # environment-specific: must be set for the server itself, never copied
                 "DJANGO_SECRET_KEY", "DJANGO_DB_NAME", "DJANGO_DB_USER", "DJANGO_DB_PASSWORD", "DJANGO_DB_HOST", "DJANGO_DB_PORT"}


def parse(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()  # last occurrence wins, like python-dotenv
    return out


def is_dev_value(key: str, value: str) -> str | None:
    if key in DEV_ONLY_KEYS:
        return "development-only setting"
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return "Windows path"
    if key.endswith(("_URL", "_ORIGINS", "_HOSTS")) and re.search(r"localhost|127\.0\.0\.1", value) and "egofit" not in value:
        return "points to localhost"
    if value == "":
        return "empty value"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True, type=Path)
    ap.add_argument("--source", required=True, type=Path)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    server, source = parse(a.server), parse(a.source)
    add, skipped = {}, {}
    for k, v in source.items():
        if k in server:
            continue
        reason = is_dev_value(k, v)
        (skipped.__setitem__(k, reason) if reason else add.__setitem__(k, v))

    print(f"server keys: {len(server)}, source keys: {len(source)}")
    print("to ADD:", ", ".join(add) or "-")
    for k, why in skipped.items():
        print(f"skipped {k}: {why} (set manually if needed)")
    if not a.write:
        print("\npreview only - re-run with --write")
        return
    if add:
        bak = a.server.with_name(f"{a.server.name}.bak_{dt.datetime.now():%Y%m%d_%H%M%S}")
        shutil.copy2(a.server, bak)
        with a.server.open("a", encoding="utf-8") as f:
            f.write(f"\n# ---- added by merge_env.py {dt.date.today()} ----\n")
            for k, v in add.items():
                f.write(f"{k}={v}\n")
        a.server.chmod(0o600)
        print(f"added {len(add)} keys; backup: {bak}")


if __name__ == "__main__":
    main()
