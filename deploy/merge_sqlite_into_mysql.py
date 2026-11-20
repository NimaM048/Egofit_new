#!/usr/bin/env python3
"""
EgoFit - merge the legacy SQLite database into the live MySQL database.

MySQL is the authoritative ("new") database. Every row that exists in SQLite but
is missing from MySQL is inserted; rows that already exist in MySQL are NEVER
modified or deleted.

How a SQLite row is recognised as "already in MySQL" (first rule that hits):
  1. merge_compatibility_id_map stored inside the SQLite file by the earlier
     MySQL->SQLite merge (source_id = MySQL id, target_id = SQLite id);
  2. any UNIQUE key of the MySQL table, compared after FK translation
     (e.g. user.phone, content_type app_label+model, day program_id+order ...);
  3. same primary key AND identical owner/creation columns
     (user_id, program_id, created_at, ...).

Missing rows are inserted in FK-dependency order inside ONE transaction:
  * the SQLite primary key is kept when it is free in MySQL, otherwise a new id
    is allocated;
  * every FK column (including self references) is translated SQLite-id -> MySQL-id;
  * django_admin_log.object_id (generic reference) is translated as well.
Before COMMIT the tool re-checks every FK in the database and re-plans the
merge (must find 0 missing rows). Any failure -> ROLLBACK, nothing changes.

Not copied (by design): django_session, account_otp, django_migrations,
SQLite internals, previous-merge bookkeeping, and tables that no longer exist
in the code (their rows are exported to JSON next to the report).

Usage
-----
  pip install PyMySQL
  # 1) dry run (default) - read only, prints the plan
  python merge_sqlite_into_mysql.py --sqlite db.sqlite3 --host 127.0.0.1 --user U --database egofitir_EgoFit_App
  # 2) apply
  python merge_sqlite_into_mysql.py ... --apply
Password: --password, or env MYSQL_PASSWORD, or an interactive prompt.
Running it again after a successful apply inserts nothing (idempotent).
"""
from __future__ import annotations

import argparse
import datetime as dt
import decimal
import getpass
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pymysql

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

# ----------------------------------------------------------------------------- policy
SKIP_TABLES = {
    "django_session": "login sessions of the old server; signed with old user ids (unsafe after id remap), users simply log in again",
    "account_otp": "short-lived one-time login codes; current code stores them encrypted (migration 0108)",
    "django_migrations": "MySQL already has every migration of the current code; SQLite-only rows belong to deleted migration files",
    "sqlite_sequence": "SQLite internal",
    "merge_compatibility_archive": "bookkeeping of the earlier merge (stays in the SQLite archive)",
    "merge_compatibility_id_map": "bookkeeping of the earlier merge (used as input only)",
}
# Natural keys of lookup tables that have no UNIQUE constraint in the DB.
# SQLite contains duplicate rows for these; they collapse onto the existing MySQL row.
EXTRA_NATURAL_KEYS = {
    "account_exerciserepetitiontype": [("reps", "goal", "method")],
    "account_exercisesettype": [("set_count", "goal", "method")],
    "account_exerciseresttype": [("rest_time", "goal", "method")],
}
# Columns that identify *whose* / *when* a row is; used to confirm a same-PK match.
IDENTITY_HINT_COLS = (
    "user_id", "owner_id", "program_id", "day_id", "athlete_id", "recipient_id", "request_id",
    "created_at", "uploaded_at", "measured_at", "recorded_at", "recorded_on", "entry_date",
    "action_time", "applied", "app", "name", "session_key", "token_hash", "order_number",
    "slug", "codename", "model", "app_label", "title",
)


# ----------------------------------------------------------------------------- normalisation
def norm(v):
    """Normalise a value so that SQLite and MySQL representations compare equal."""
    if v is None:
        return None
    if isinstance(v, bytes):
        try:
            v = v.decode("utf-8")
        except UnicodeDecodeError:
            return v
    if isinstance(v, dt.datetime):
        return v.replace(tzinfo=None).isoformat(" ")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, dt.timedelta):  # MySQL TIME
        s = int(v.total_seconds())
        return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
    if isinstance(v, (decimal.Decimal, float)):
        return round(float(v), 6)
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        if len(s) >= 19 and s[4] == "-" and s[7] == "-" and s[10] in " T" and s[13] == ":":
            try:
                return parse_dt(s).replace(tzinfo=None).isoformat(" ")
            except ValueError:
                pass
        if len(s) in (5, 8) and s[2:3] == ":":  # TIME text
            return s if len(s) == 8 else s + ":00"
        if s[:1] in "[{":
            try:
                return json.dumps(json.loads(s), sort_keys=True, ensure_ascii=False)
            except ValueError:
                pass
        return v
    if isinstance(v, int):
        return round(float(v), 6)
    return v


def parse_dt(s: str) -> dt.datetime:
    """fromisoformat that also works on Python < 3.11 (fractions of any length, 'Z')."""
    s = s.strip().replace("T", " ").replace("Z", "+00:00")
    tz = ""
    if len(s) > 19 and s[-6] in "+-" and s[-3] == ":":
        s, tz = s[:-6], s[-6:]
    if "." in s:
        head, frac = s.split(".", 1)
        s = f"{head}.{(frac + '000000')[:6]}"
    return dt.datetime.fromisoformat(s + tz)


def same(a, b):
    """Value equality across engines (also '84.5' == Decimal('84.5'))."""
    na, nb = norm(a), norm(b)
    if na == nb:
        return True
    if isinstance(na, float) != isinstance(nb, float):
        try:
            return round(float(na), 6) == round(float(nb), 6)
        except (TypeError, ValueError):
            return False
    return False


def jsonable(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat(" ") if isinstance(v, dt.datetime) else v.isoformat()
    if isinstance(v, (decimal.Decimal,)):
        return str(v)
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v


# ----------------------------------------------------------------------------- merger
class Merger:
    def __init__(self, sqlite_path, conn):
        self.sqlite_path = sqlite_path
        self.sq = sqlite3.connect(f"file:{Path(sqlite_path).as_posix()}?mode=ro", uri=True)
        self.my = conn
        self.cur = conn.cursor()
        self.load_schema()

    def M(self, q, a=None):
        self.cur.execute(q, a)
        return self.cur.fetchall()

    def Q(self, q, a=()):
        return self.sq.execute(q, a).fetchall()

    # -------------------------------------------------- schema
    def load_schema(self):
        self.my_tables = {r[0] for r in self.M(
            "select table_name from information_schema.tables where table_schema=database() and table_type='BASE TABLE'")}
        self.sq_tables = {r[0] for r in self.Q("select name from sqlite_master where type='table'")}
        self.cols, self.coltype = defaultdict(list), {}
        for t, c, typ in self.M("select table_name,column_name,data_type from information_schema.columns "
                                "where table_schema=database() order by table_name, ordinal_position"):
            self.cols[t].append(c)
            self.coltype[(t, c)] = typ
        self.pk = {t: c for t, c in self.M("select table_name,column_name from information_schema.key_column_usage "
                                            "where table_schema=database() and constraint_name='PRIMARY'")}
        self.fks = defaultdict(dict)
        for t, c, rt in self.M("select table_name,column_name,referenced_table_name from information_schema.key_column_usage "
                               "where table_schema=database() and referenced_table_name is not null"):
            self.fks[t][c] = rt
        self.uniques = defaultdict(list)
        for t, _name, cols in self.M(
                "select table_name,index_name,group_concat(column_name order by seq_in_index) from information_schema.statistics "
                "where table_schema=database() and non_unique=0 and index_name<>'PRIMARY' group by 1,2"):
            self.uniques[t].append(tuple(cols.split(",")))
        for t, keys in EXTRA_NATURAL_KEYS.items():
            self.uniques[t].extend(k for k in keys if all(c in self.cols[t] for c in k))

    def topo_order(self, tables):
        deps = {t: {rt for rt in self.fks[t].values() if rt != t and rt in tables} for t in tables}
        order, done = [], set()
        while len(done) < len(tables):
            ready = sorted(t for t in tables if t not in done and deps[t] <= done)
            if not ready:
                raise RuntimeError(f"FK cycle among {sorted(set(tables) - done)}")
            order += ready
            done |= set(ready)
        return order

    # -------------------------------------------------- planning (read only)
    def plan(self):
        self.orphan_tables = sorted(t for t in self.sq_tables - self.my_tables if t not in SKIP_TABLES)
        common = sorted((self.sq_tables & self.my_tables) - set(SKIP_TABLES))
        self.order = self.topo_order(common)
        idmap = defaultdict(dict)
        if "merge_compatibility_id_map" in self.sq_tables:
            for t, s, tg in self.Q("select source_table, source_id, target_id from merge_compatibility_id_map"):
                idmap[t][int(tg)] = int(s)
        self.map = defaultdict(dict)        # table -> {sqlite_pk: mysql_pk}
        self.new_rows = defaultdict(list)   # table -> [(sqlite_row, mysql_pk)]
        self.use_cols, self.stats, self.warnings = {}, {}, []
        self.diffs = defaultdict(Counter)
        for t in self.order:
            self._plan_table(t, idmap.get(t, {}))
        return self

    def _tr(self, t, c, v):
        if v is None or c not in self.fks[t]:
            return v
        return self.map[self.fks[t][c]].get(v, ("__UNMAPPED__", self.fks[t][c], v))

    def _plan_table(self, t, idmap):
        pk = self.pk[t]
        scols = [r[1] for r in self.Q(f'pragma table_info("{t}")')]
        use = [c for c in self.cols[t] if c in scols]
        lost = [c for c in scols if c not in self.cols[t]]
        if lost:
            self.warnings.append(f"{t}: SQLite columns not present in MySQL (ignored): {lost}")
        self.use_cols[t] = use
        srows = [dict(zip(use, r)) for r in self.Q(f'select {",".join(chr(34) + c + chr(34) for c in use)} from "{t}" order by 1')]
        mrows = {r[self.cols[t].index(pk)]: dict(zip(self.cols[t], r))
                 for r in self.M(f"select {','.join('`' + c + '`' for c in self.cols[t])} from `{t}`")}
        extra = set(EXTRA_NATURAL_KEYS.get(t, []))
        uidx, xidx = {}, {}
        for uk in self.uniques[t]:
            d = defaultdict(list)
            for mid, r in sorted(mrows.items()):
                key = tuple(norm(r[c]) for c in uk)
                if None not in key:
                    d[key].append(mid)
            (xidx if uk in extra else uidx)[uk] = d
        self_fk = {c for c, rt in self.fks[t].items() if rt == t}
        claimed, st, pending, matched = set(), Counter(), [], {}

        def key_of(tr, uk):
            if any(c not in tr for c in uk):
                return None
            key = tuple(norm(tr[c]) for c in uk)
            return None if None in key or any(isinstance(k, tuple) for k in key) else key

        rows_tr = []
        # pass 1: previous id map, real UNIQUE keys, same primary key with identical owner/creation columns
        for r in srows:
            sid = r[pk]
            tr = {c: (v if c in self_fk else self._tr(t, c, v)) for c, v in r.items()}
            rows_tr.append((r, tr))
            mid = how = None
            if sid in idmap and idmap[sid] in mrows and idmap[sid] not in claimed:
                mid, how = idmap[sid], "id_map"
            if mid is None:
                for uk, d in uidx.items():
                    key = key_of(tr, uk)
                    if key is not None and key in d:
                        mid, how = d[key][0], "unique_key"
                        break
            if mid is None and sid in mrows and sid not in claimed:
                mr = mrows[sid]
                hints = [c for c in IDENTITY_HINT_COLS if c in tr and c in mr]
                if hints:
                    bad = [c for c in hints if not same(tr[c], mr[c])]
                    ok = not bad
                else:
                    bad = [c for c in use if not same(tr[c], mr[c])]
                    ok = len(bad) <= 0.2 * len(use)
                if ok:
                    mid, how = sid, "same_pk"
                elif not xidx:
                    self.warnings.append(f"{t} id={sid}: id exists in MySQL but is a different record "
                                         f"(differs on {bad}); inserted with a new id")
            if mid is not None:
                if mid in claimed:
                    self.warnings.append(f"{t}: MySQL id {mid} matched by two SQLite rows (second: {sid}, via {how})")
                claimed.add(mid)
                matched[sid] = (mid, how)
        # pass 2: lookup-table natural keys (no DB constraint); duplicates inside SQLite collapse
        for r, tr in rows_tr:
            sid = r[pk]
            if sid in matched:
                continue
            for uk, d in xidx.items():
                key = key_of(tr, uk)
                if key is not None and key in d:
                    free = [x for x in d[key] if x not in claimed]
                    mid = free[0] if free else d[key][0]
                    matched[sid] = (mid, "natural_key" if free else "sqlite_duplicate_collapsed")
                    claimed.add(mid)
                    break
        for r, tr in rows_tr:
            sid = r[pk]
            if sid in matched:
                mid, how = matched[sid]
                self.map[t][sid] = mid
                st["matched_" + how] += 1
                for c in use:
                    if c != pk and c not in self_fk and not same(tr[c], mrows[mid][c]):
                        self.diffs[t][c] += 1
            else:
                unm = [v for v in tr.values() if isinstance(v, tuple)]
                if unm:
                    self.warnings.append(f"{t} sqlite id={sid}: parent rows missing everywhere {unm}")
                pending.append(r)
        used = set(mrows)
        nxt = max([0, *mrows, self.auto_inc(t) - 1]) + 1
        for r in pending:
            sid = r[pk]
            if isinstance(sid, int) and sid not in used:
                nid = sid
                st["insert_same_id"] += 1
            else:
                while nxt in used:
                    nxt += 1
                nid = nxt
                st["insert_new_id"] += 1
            used.add(nid)
            self.map[t][sid] = nid
            self.new_rows[t].append((r, nid))
        st["sqlite_rows"], st["mysql_rows"] = len(srows), len(mrows)
        st["mysql_only_rows"] = len(set(mrows) - claimed)
        self.stats[t] = st

    def auto_inc(self, t):
        r = self.M("select auto_increment from information_schema.tables where table_schema=database() and table_name=%s", (t,))
        return int(r[0][0] or 1) if r else 1

    @property
    def total_new(self):
        return sum(len(v) for v in self.new_rows.values())

    # -------------------------------------------------- writing
    def _convert(self, t, c, v):
        if v is None:
            return None
        typ = self.coltype[(t, c)]
        if typ in ("datetime", "timestamp") and isinstance(v, str):
            d = parse_dt(v)
            return d.astimezone(dt.timezone.utc).replace(tzinfo=None) if d.tzinfo else d
        if typ == "date" and isinstance(v, str):
            return dt.date.fromisoformat(v[:10])
        if typ == "json" and not isinstance(v, str):
            return json.dumps(v, ensure_ascii=False)
        return v

    def _row_values(self, t, r, nid):
        pk, cols = self.pk[t], self.use_cols[t]
        vals = []
        for c in cols:
            v = r[c]
            if c == pk:
                v = nid
            elif c in self.fks[t] and v is not None:
                m = self.map[self.fks[t][c]].get(v)
                if m is None:
                    raise RuntimeError(f"{t}.{c}={v}: referenced {self.fks[t][c]} row exists in neither database")
                v = m
            vals.append(self._convert(t, c, v))
        if t == "django_admin_log":
            ct = self.Q("select app_label, model from django_content_type where id=?", (r["content_type_id"],))
            oid = r.get("object_id")
            if ct and oid is not None and str(oid).isdigit():
                new = self.map.get(f"{ct[0][0]}_{ct[0][1]}", {}).get(int(oid))
                if new is not None and new != int(oid):
                    vals[cols.index("object_id")] = str(new)
        return vals

    def apply(self):
        self.cur.execute("SET time_zone='+00:00'")
        self.cur.execute("SET FOREIGN_KEY_CHECKS=0")  # rows are inserted parents-first; checked explicitly below
        inserted = {}
        for t in self.order:
            if not self.new_rows[t]:
                continue
            cols = self.use_cols[t]
            sql = (f"INSERT INTO `{t}` ({','.join('`' + c + '`' for c in cols)}) "
                   f"VALUES ({','.join(['%s'] * len(cols))})")
            self.cur.executemany(sql, [self._row_values(t, r, nid) for r, nid in self.new_rows[t]])
            inserted[t] = len(self.new_rows[t])
        self.cur.execute("SET FOREIGN_KEY_CHECKS=1")
        return inserted

    def fk_violations(self):
        bad = []
        for t, fk in self.fks.items():
            for c, rt in fk.items():
                n = self.M(f"select count(*) from `{t}` x left join `{rt}` p on p.`{self.pk[rt]}`=x.`{c}` "
                           f"where x.`{c}` is not null and p.`{self.pk[rt]}` is null")[0][0]
                if n:
                    bad.append({"table": t, "column": c, "references": rt, "broken_rows": n})
        return bad

    # -------------------------------------------------- reporting
    def report(self):
        return {
            "tables": {t: dict(self.stats[t]) for t in self.order if self.stats[t]["sqlite_rows"] or self.stats[t]["mysql_rows"]},
            "rows_to_insert": {t: len(v) for t, v in self.new_rows.items() if v},
            "total_rows_to_insert": self.total_new,
            "id_translation_for_inserted_rows": {
                t: {str(r[self.pk[t]]): nid for r, nid in v if r[self.pk[t]] != nid} for t, v in self.new_rows.items() if v},
            "value_differences_on_matched_rows_mysql_kept": {t: dict(c) for t, c in self.diffs.items() if c},
            "skipped_tables": {t: why for t, why in SKIP_TABLES.items() if t in self.sq_tables},
            "sqlite_tables_without_model_exported_to_json": self.orphan_tables,
            "warnings": self.warnings,
        }

    def export_orphans(self, out_dir: Path):
        out = {}
        for t in self.orphan_tables:
            cols = [r[1] for r in self.Q(f'pragma table_info("{t}")')]
            rows = [dict(zip(cols, map(jsonable, r))) for r in self.Q(f'select * from "{t}"')]
            p = out_dir / f"orphan_{t}.json"
            p.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            out[t] = str(p)
        return out


def print_plan(m: Merger):
    print(f"{'table':46} {'sqlite':>7} {'mysql':>7} {'matched':>8} {'INSERT':>7} {'mysql-only':>10}")
    for t in m.order:
        s = m.stats[t]
        if not (s["sqlite_rows"] or s["mysql_rows"]):
            continue
        matched = sum(v for k, v in s.items() if k.startswith("matched_"))
        new = s["insert_same_id"] + s["insert_new_id"]
        print(f"{t:46} {s['sqlite_rows']:7} {s['mysql_rows']:7} {matched:8} {new:7} {s['mysql_only_rows']:10}"
              + ("   <--" if new else ""))
    print(f"\nTotal rows to insert: {m.total_new}")
    if m.diffs:
        print("Value differences on matched rows (MySQL value is kept):",
              {t: dict(c) for t, c in m.diffs.items() if c})
    for w in m.warnings:
        print("WARNING:", w)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sqlite", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--user", required=True)
    ap.add_argument("--password")
    ap.add_argument("--database", default="egofitir_EgoFit_App")
    ap.add_argument("--apply", action="store_true", help="write to MySQL (default: dry run)")
    ap.add_argument("--report-dir", default="merge_report")
    a = ap.parse_args()
    pwd = a.password if a.password is not None else os.getenv("MYSQL_PASSWORD")
    if pwd is None:
        pwd = getpass.getpass("MySQL password: ")
    conn = pymysql.connect(host=a.host, port=a.port, user=a.user, password=pwd, database=a.database,
                           charset="utf8mb4", autocommit=False)
    rep_dir = Path(a.report_dir)
    rep_dir.mkdir(parents=True, exist_ok=True)

    m = Merger(a.sqlite, conn).plan()
    print_plan(m)
    report = m.report()
    report["orphan_exports"] = m.export_orphans(rep_dir)
    report["mode"] = "apply" if a.apply else "dry-run"
    report["started_at"] = dt.datetime.now().isoformat(timespec="seconds")

    if a.apply and m.total_new:
        conn.begin()
        try:
            inserted = m.apply()
            bad = m.fk_violations()
            if bad:
                raise RuntimeError(f"FK violations after insert: {bad}")
            check = Merger(a.sqlite, conn).plan()   # same transaction: must find nothing left to insert
            if check.total_new:
                raise RuntimeError(f"re-plan still finds {check.total_new} missing rows: "
                                   f"{ {t: len(v) for t, v in check.new_rows.items() if v} }")
            lost = {t: s["mysql_rows"] for t, s in check.stats.items() if s["mysql_rows"] < m.stats[t]["mysql_rows"]}
            if lost:
                raise RuntimeError(f"row counts decreased: {lost}")
            conn.commit()
            report["inserted"] = inserted
            report["verification"] = {"fk_violations": 0, "replan_missing_rows": 0, "status": "COMMITTED"}
            print(f"\nCOMMITTED: {sum(inserted.values())} rows inserted; FK check OK; re-plan finds 0 missing rows.")
        except Exception as e:
            conn.rollback()
            report["verification"] = {"status": "ROLLED BACK", "error": str(e)}
            (rep_dir / "merge_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            print(f"\nROLLED BACK - nothing was changed. Reason: {e}")
            sys.exit(1)
    elif a.apply:
        print("\nNothing to insert - MySQL already contains every SQLite row.")
    else:
        print("\nDRY RUN - nothing written. Re-run with --apply to insert.")
    (rep_dir / "merge_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"Report: {rep_dir / 'merge_report.json'}")


if __name__ == "__main__":
    main()
