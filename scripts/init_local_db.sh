#!/usr/bin/env bash
set -euo pipefail
# Applies schema + seed to a local Homebrew Postgres database named hoodwise.
psql postgres -c "CREATE DATABASE hoodwise;" 2>/dev/null || true
psql hoodwise -v ON_ERROR_STOP=1 -f db/schema.sql
psql hoodwise -v ON_ERROR_STOP=1 -f db/seed.sql
echo "hoodwise database ready"
