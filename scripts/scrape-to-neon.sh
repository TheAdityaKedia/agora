#!/usr/bin/env bash
# Scrape sources from THIS machine (residential IP) into the production Neon DB.
#
# For sources that fail from GitHub's datacenter runners (see
# service/data/local_only_sources.txt and future-sources.md → "Sources that
# need fixing"). Rows land in Neon; the next CI run of scrape.yml exports them
# into events.json and ships it — pass --ship to trigger that run now.
#
#   scripts/scrape-to-neon.sh --blocked           # just the known-trouble sources
#   scripts/scrape-to-neon.sh --blocked --ship    # ...then dispatch a CI run to ship them
#   scripts/scrape-to-neon.sh gamh.com ybca.org   # any --sources substrings
#   scripts/scrape-to-neon.sh -y                  # ALL sources (a full backfill)
#
# Options: --blocked  --ship  --no-build (skip image rebuild)  -y (no prompt)
# Needs DATABASE_URL in .env pointing at Neon (written by `neon link`).
# Classification is skipped here; the CI run tags new shows.
set -euo pipefail

cd "$(dirname "$0")/.."

blocked=0 ship=0 build=1 yes=0
sources=()
while [ $# -gt 0 ]; do
  case "$1" in
    --blocked) blocked=1 ;;
    --ship) ship=1 ;;
    --no-build) build=0 ;;
    -y|--yes) yes=1 ;;
    -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) sources+=("$1") ;;
  esac
  shift
done

if [ "$blocked" = 1 ]; then
  while IFS= read -r line; do
    line="${line%%#*}"; line="$(echo "$line" | tr -d '[:space:]')"
    [ -n "$line" ] && sources+=("$line")
  done < service/data/local_only_sources.txt
fi

[ -f .env ] || { echo "no .env — run \`neon link\` first" >&2; exit 1; }
set -a; . ./.env; set +a
case "${DATABASE_URL:-}" in
  "") echo "DATABASE_URL not set in .env" >&2; exit 1 ;;
  *localhost*|*@db:*|*127.0.0.1*) echo "DATABASE_URL points at a local DB, not Neon" >&2; exit 1 ;;
esac

# One writer at a time: don't overlap a CI run on the same DB.
if command -v gh >/dev/null 2>&1; then
  running="$(gh run list --workflow scrape.yml --branch main --status in_progress \
             --json databaseId -q 'length' 2>/dev/null || echo 0)"
  if [ "${running:-0}" != "0" ]; then
    echo "a scrape.yml run is in progress on main — wait for it to finish" >&2; exit 1
  fi
fi

if [ ${#sources[@]} -eq 0 ]; then
  echo "No sources given: this scrapes ALL sources into production Neon."
  if [ "$yes" != 1 ]; then
    read -r -p "Continue? [y/N] " reply
    [ "$reply" = y ] || [ "$reply" = Y ] || exit 1
  fi
  source_args=()
else
  echo "Sources: ${sources[*]}"
  source_args=(--sources "${sources[@]}")
fi

[ "$build" = 1 ] && docker compose build -q scraper

# --no-deps: don't start the local Compose Postgres. The manifest is written
# inside the container and discarded — CI owns events.json.
docker compose run --rm --no-deps -e DATABASE_URL -e EVENTS_JSON_PATH=/tmp/events.json \
  scraper python main.py --no-classify ${source_args[@]+"${source_args[@]}"}

if [ "$ship" = 1 ]; then
  echo "Dispatching scrape.yml on main to export + ship..."
  if [ ${#sources[@]} -eq 0 ]; then
    gh workflow run scrape.yml --ref main
  else
    gh workflow run scrape.yml --ref main -f sources="${sources[*]}"
  fi
  echo "Follow it with: gh run watch \$(gh run list --workflow scrape.yml --limit 1 --json databaseId -q '.[0].databaseId')"
else
  echo "Done. Rows are in Neon; they ship with the next scrape.yml run (daily ~3:23am PT, or rerun with --ship)."
fi
