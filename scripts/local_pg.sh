#!/usr/bin/env bash
# Fallback for machines WITHOUT Docker: run PostgreSQL 16 + pgvector from the `pgserver`
# PyPI wheel (bundles postgres binaries and the vector extension). Data in ./.pgdata.
# Usage: scripts/local_pg.sh start|stop|status
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.pgserver-venv"
DATA="$ROOT/.pgdata/d"
if [ ! -x "$VENV/bin/python" ]; then
  uv venv -q -p 3.11 "$VENV" && VIRTUAL_ENV="$VENV" uv pip install -q "pgserver==0.1.4"
fi
BIN="$("$VENV/bin/python" -c 'import pgserver,os;print(os.path.join(os.path.dirname(pgserver.__file__),"pginstall","bin"))')"
case "${1:-start}" in
  start)
    if [ ! -d "$DATA" ]; then
      mkdir -p "$DATA"
      "$BIN/initdb" -D "$DATA" -U opspilot --auth=trust -E UTF8 >/dev/null
    fi
    "$BIN/pg_ctl" -D "$DATA" -o "-p 5432 -k /tmp -c listen_addresses=127.0.0.1" \
      -l "$ROOT/.pgdata/postgres.log" -w start
    for db in opspilot opspilot_test; do
      "$BIN/psql" -h 127.0.0.1 -U opspilot -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$db'" \
        | grep -q 1 || "$BIN/psql" -h 127.0.0.1 -U opspilot -d postgres -qc "CREATE DATABASE $db"
    done ;;
  stop) "$BIN/pg_ctl" -D "$DATA" stop ;;
  status) "$BIN/pg_ctl" -D "$DATA" status ;;
esac
