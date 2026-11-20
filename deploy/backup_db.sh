#!/usr/bin/env bash
# Daily MySQL backup for EgoFit (cron). Reads credentials from the app .env,
# writes ~/egofit-backups/daily/egofit_<date>.sql.gz and keeps KEEP_DAYS days.
set -euo pipefail
APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OUT_DIR="${OUT_DIR:-$HOME/egofit-backups/daily}"
KEEP_DAYS="${KEEP_DAYS:-14}"
mkdir -p "$OUT_DIR"; chmod 700 "$OUT_DIR"

get() { sed -n "s/^$1=//p" "$APP_DIR/.env" | tail -1 | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }
DB_NAME="$(get DJANGO_DB_NAME)"; DB_USER="$(get DJANGO_DB_USER)"
DB_HOST="$(get DJANGO_DB_HOST)"; DB_PORT="$(get DJANGO_DB_PORT)"
export MYSQL_PWD="$(get DJANGO_DB_PASSWORD)"

FILE="$OUT_DIR/egofit_$(date +%Y%m%d_%H%M).sql.gz"
mysqldump --single-transaction --routines --triggers --no-tablespaces \
  -h "${DB_HOST:-localhost}" -P "${DB_PORT:-3306}" -u "$DB_USER" "${DB_NAME:-egofitir_EgoFit_App}" | gzip -9 > "$FILE.tmp"
mv "$FILE.tmp" "$FILE"; chmod 600 "$FILE"
find "$OUT_DIR" -name 'egofit_*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "$(date '+%F %T') backup ok: $FILE ($(du -h "$FILE" | cut -f1))"
