#!/usr/bin/env python3
"""
Run one of the CELERY_BEAT_SCHEDULE tasks synchronously - used from cron on
hosts where a long-running Celery beat/worker is not available (cPanel).

    python deploy/run_periodic_task.py account.cleanup_expired_otps
    python deploy/run_periodic_task.py --all            # every scheduled task once

A lock file per task prevents overlapping runs when a run takes longer than
its cron interval.
"""
from __future__ import annotations

import fcntl
import os
import sys
import time
from importlib import import_module
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.chdir(BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sport_shop.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

TASK_MODULES = {
    "account.cleanup_expired_otps": "account.tasks.otp_tasks",
    "account.send_due_mood_reminders": "account.tasks.mood_reminder_tasks",
    "account.scan_athlete_health_trends": "account.tasks.health_alert_tasks",
}


def run(task_name: str) -> None:
    module = import_module(TASK_MODULES[task_name])
    func = getattr(module, task_name.split(".", 1)[1])
    lock_path = BASE_DIR / "tmp" / f"cron-{task_name}.lock"
    lock_path.parent.mkdir(exist_ok=True)
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"{task_name}: previous run still active, skipping")
            return
        started = time.monotonic()
        result = func()  # plain call: runs synchronously in this process
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {task_name} -> {result} ({time.monotonic() - started:.1f}s)")


def main() -> int:
    args = sys.argv[1:]
    if args == ["--all"]:
        args = [entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()]
    unknown = [a for a in args if a not in TASK_MODULES]
    if not args or unknown:
        print(f"usage: run_periodic_task.py <{'|'.join(TASK_MODULES)}> | --all", file=sys.stderr)
        return 2
    for name in args:
        run(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
