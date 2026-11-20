"""WSGI entry point for cPanel deployments using Phusion Passenger."""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sport_shop.settings")

from sport_shop.wsgi import application  # noqa: E402
