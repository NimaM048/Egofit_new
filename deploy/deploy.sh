#!/usr/bin/env bash
# EgoFit release script for cPanel (Phusion Passenger).
#
# Pulls the latest code from GitHub, installs dependencies, backs up the
# database when migrations are pending, migrates, collects static files and
# restarts Passenger. Safe to run repeatedly.
#
#   bash deploy/deploy.sh            # deploy origin/main
#   BRANCH=staging bash deploy/deploy.sh
#
# Settings (env vars, all optional):
#   APP_DIR   application root (default: directory containing this script/..)
#   VENV      virtualenv dir   (default: auto-detect cPanel ~/virtualenv/<app>/<ver>)
#   BRANCH    git branch       (default: main)
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BRANCH="${BRANCH:-main}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/egofit-backups}"
cd "$APP_DIR"

log() { printf '\n==> %s\n' "$*"; }

# ---- virtualenv -------------------------------------------------------------
if [[ -z "${VENV:-}" ]]; then
  APP_NAME="$(basename "$APP_DIR")"
  VENV="$(ls -d "$HOME"/virtualenv/"$APP_NAME"/*/ 2>/dev/null | sort -V | tail -1 || true)"
fi
if [[ -z "$VENV" || ! -f "$VENV/bin/activate" ]]; then
  echo "Virtualenv not found. Create the app in cPanel > Setup Python App, or set VENV=..." >&2
  exit 1
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

# ---- code -------------------------------------------------------------------
if [[ "${SKIP_PULL:-0}" != "1" ]]; then
  log "Fetching origin/$BRANCH"
  OLD_REQ_HASH="$(sha1sum requirements.txt 2>/dev/null | cut -d' ' -f1 || true)"
  git fetch --prune origin "$BRANCH"
  # untracked/ignored files (.env, media, tmp) are never touched by reset
  git reset --hard "origin/$BRANCH"
  NEW_REQ_HASH="$(sha1sum requirements.txt | cut -d' ' -f1)"
else
  OLD_REQ_HASH=""; NEW_REQ_HASH="force"
fi
log "Now at $(git log -1 --format='%h %s')"

# ---- dependencies -------------------------------------------------------------
if [[ "$OLD_REQ_HASH" != "$NEW_REQ_HASH" || "${FORCE_PIP:-0}" == "1" ]]; then
  log "Installing requirements"
  python -m pip install --upgrade pip -q
  python -m pip install --index-url "${PIP_INDEX_URL:-https://pypi.org/simple}" -r requirements.txt -q
else
  log "requirements.txt unchanged - skipping pip"
fi

# ---- sanity checks ----------------------------------------------------------
[[ -f .env ]] || { echo ".env missing in $APP_DIR" >&2; exit 1; }
log "Django system check"
python manage.py check --deploy --fail-level ERROR

# ---- database ---------------------------------------------------------------
if python manage.py migrate --check >/dev/null 2>&1; then
  log "No pending migrations"
else
  log "Pending migrations:"
  python manage.py showmigrations --plan | grep '\[ \]' || true
  mkdir -p "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"
  STAMP="$(date +%Y%m%d_%H%M%S)"
  log "Backing up database to $BACKUP_DIR/db_$STAMP.sql.gz"
  python - "$BACKUP_DIR/db_$STAMP.sql.gz" <<'PY'
import gzip, os, subprocess, sys
from pathlib import Path
env = {}
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
cmd = ["mysqldump", "--single-transaction", "--routines", "--triggers", "--no-tablespaces",
       "-h", env.get("DJANGO_DB_HOST", "localhost"), "-P", env.get("DJANGO_DB_PORT", "3306"),
       "-u", env["DJANGO_DB_USER"], env.get("DJANGO_DB_NAME", "egofitir_EgoFit_App")]
with gzip.open(sys.argv[1], "wb") as out:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, env={**os.environ, "MYSQL_PWD": env["DJANGO_DB_PASSWORD"]}, check=True)
    out.write(p.stdout)
print("backup ok:", sys.argv[1])
PY
  log "Applying migrations"
  python manage.py migrate --noinput
fi

# ---- static -----------------------------------------------------------------
log "Collecting static files"
python manage.py collectstatic --noinput -v 0

# ---- restart ----------------------------------------------------------------
mkdir -p tmp
touch tmp/restart.txt
log "Deployed $(git log -1 --format='%h') - Passenger restart requested"
