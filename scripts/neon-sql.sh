#!/usr/bin/env bash
# Run SQL against Agora's Neon database from any Mac or Linux machine.
#
#   scripts/neon-sql.sh --query """SELECT count(*) FROM events;"""
#   scripts/neon-sql.sh --branch ci-test --query """SELECT * FROM events LIMIT 5;"""
#   scripts/neon-sql.sh --query """DELETE FROM events WHERE sources->>0 = 'X';"""
#
# Options:
#   --query SQL     the SQL to run (required); several statements are fine
#   --branch NAME   Neon branch (default: production; ci-test is the test DB)
#   --yes           skip the confirmation for writes on production
#
# The query runs in ONE transaction and stops at the first error, so a
# multi-statement change applies completely or not at all. A query that can
# change data (INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, …) on
# production asks you to type the branch name first. Check with a SELECT
# before you DELETE.
#
# Installs what's missing — the Neon CLI (Homebrew, else npm) and psql
# (Homebrew libpq, else apt/dnf/yum/apk) — and asks for what isn't set:
#   NEON_API_KEY     needed unless this machine ran `neon auth` already.
#                    Create one: https://console.neon.tech/app/settings/api-keys
#   NEON_PROJECT_ID  defaults to the repo's .neon link, else Agora's project.
set -euo pipefail

AGORA_PROJECT_ID="wandering-math-55485491"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

query="" branch="production" yes=0
while [ $# -gt 0 ]; do
  case "$1" in
    --query) [ $# -ge 2 ] || { echo "--query needs a value" >&2; exit 2; }; query="$2"; shift ;;
    --query=*) query="${1#--query=}" ;;
    --branch) [ $# -ge 2 ] || { echo "--branch needs a value" >&2; exit 2; }; branch="$2"; shift ;;
    --branch=*) branch="${1#--branch=}" ;;
    -y|--yes) yes=1 ;;
    -h|--help) sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done
[ -n "${query//[[:space:]]/}" ] || { echo "--query is required (see --help)" >&2; exit 2; }

say() { printf '%s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }
# Prompts read from the terminal, so they work even when stdin is redirected.
tty_ok() { [ -r /dev/tty ] && { : </dev/tty; } 2>/dev/null; }

# Root runs commands directly; anyone else goes through sudo.
as_root() {
  if [ "$(id -u)" -eq 0 ]; then "$@"
  elif have sudo; then sudo "$@"
  else say "Need root to run: $*  (no sudo found)"; exit 1
  fi
}

# Install a Linux package with whichever package manager this distro has.
# Usage: pkg_install <apt name> <dnf/yum name> <apk name>
# Installer chatter goes to stderr: stdout is only ever the query's output.
pkg_install() {
  if have apt-get; then
    as_root apt-get update -qq >&2 && DEBIAN_FRONTEND=noninteractive as_root apt-get install -y -qq "$1" >&2
  elif have dnf; then as_root dnf install -y -q "$2" >&2
  elif have yum; then as_root yum install -y -q "$2" >&2
  elif have apk; then as_root apk add --no-cache "$3" >&2
  else say "No supported package manager (apt, dnf, yum, apk); install $1 yourself."; exit 1
  fi
}

# --- Node.js (the Neon CLI's npm package needs >=20.19: `npm view neonctl engines`)
NODE_MIN=20.19
node_ok() {
  have node && have npm || return 1
  # Older Node makes npm fall back to an ancient neonctl that half-works.
  node -e 'const [a,b]=process.versions.node.split(".").map(Number), [x,y]=process.argv[1].split(".").map(Number);
           process.exit(a>x || (a===x && b>=y) ? 0 : 1)' "$NODE_MIN" 2>/dev/null
}

download() {  # download <url> <file>
  if have curl; then curl -fsSL "$1" -o "$2"
  elif have wget; then wget -qO "$2" "$1"
  else pkg_install curl curl curl; curl -fsSL "$1" -o "$2"
  fi
}

# Distro packages are too old on most LTS releases (Ubuntu 24.04 ships Node
# 18, which the current Neon CLI crashes on), so install the official Node 22
# build into ~/.local/node: no root, any glibc distro or Mac, x64 or arm64.
ensure_node() {
  node_ok && return
  say "Node.js $NODE_MIN+ not found; installing it..."
  if have apk; then  # Alpine is musl; nodejs.org only builds for glibc
    pkg_install nodejs nodejs nodejs; pkg_install npm npm npm
    node_ok && return
    say "Alpine's Node is older than $NODE_MIN; upgrade Alpine or install Node yourself."; exit 1
  fi
  local os arch
  case "$(uname -s)" in Linux) os=linux ;; Darwin) os=darwin ;; *) say "Unsupported OS: $(uname -s)"; exit 1 ;; esac
  case "$(uname -m)" in x86_64|amd64) arch=x64 ;; arm64|aarch64) arch=arm64 ;; *) say "Unsupported CPU: $(uname -m)"; exit 1 ;; esac
  have tar || pkg_install tar tar tar
  local base="https://nodejs.org/dist/latest-v22.x" tmp file sum
  tmp="$(mktemp -d)"
  download "$base/SHASUMS256.txt" "$tmp/SHASUMS256.txt"
  file="$(grep -oE "node-v[0-9.]+-$os-$arch\.tar\.gz" "$tmp/SHASUMS256.txt" | head -1)"
  [ -n "$file" ] || { say "No Node build for $os-$arch."; exit 1; }
  download "$base/$file" "$tmp/$file"
  # Verify before unpacking anything.
  if have sha256sum; then sum="$(sha256sum "$tmp/$file" | cut -d' ' -f1)"
  else sum="$(shasum -a 256 "$tmp/$file" | cut -d' ' -f1)"; fi
  grep -q "^$sum  $file\$" "$tmp/SHASUMS256.txt" || { say "Checksum mismatch for $file; not installing."; exit 1; }
  rm -rf "$HOME/.local/node" && mkdir -p "$HOME/.local/node"
  tar -xzf "$tmp/$file" -C "$HOME/.local/node" --strip-components=1
  rm -rf "$tmp"
  export PATH="$HOME/.local/node/bin:$PATH"
  node_ok || { say "Installed Node but it doesn't run."; exit 1; }
}

# --- the Neon CLI (`neon`; older installs call it `neonctl`) -----------------
# A Neon CLI that is already installed but runs on too old a Node fails the
# version probe, so it counts as missing and is reinstalled.
neon_runs() { "$1" --version >/dev/null 2>&1; }

ensure_neon() {
  # Where earlier runs of this script installed things (not on PATH by default).
  for d in "$HOME/.local/bin" "$HOME/.local/node/bin"; do
    [ -d "$d" ] && export PATH="$d:$PATH"
  done
  if have neon && neon_runs neon; then NEON=neon; return; fi
  if have neonctl && neon_runs neonctl; then NEON=neonctl; return; fi
  say "Neon CLI not found (or it doesn't run); installing it..."
  if have brew; then
    brew install neonctl >&2
  else
    ensure_node
    # A global npm install into a root-owned prefix would need sudo; install
    # into ~/.local instead, which needs no privileges.
    if [ -w "$(npm prefix -g)" ]; then
      npm install -g --silent neonctl >&2
    else
      npm install -g --silent --prefix "$HOME/.local" neonctl >&2
      export PATH="$HOME/.local/bin:$PATH"
    fi
  fi
  if have neon && neon_runs neon; then NEON=neon
  elif have neonctl && neon_runs neonctl; then NEON=neonctl
  else say "Installed the Neon CLI but it doesn't run: $(neon --version 2>&1 | tail -1)"; exit 1
  fi
}

# --- psql (the Neon CLI's `psql` command shells out to it) ------------------
ensure_psql() {
  have psql && return
  # Homebrew's libpq is keg-only: installed but not linked onto PATH.
  if have brew && [ -x "$(brew --prefix)/opt/libpq/bin/psql" ]; then
    export PATH="$(brew --prefix)/opt/libpq/bin:$PATH"; return
  fi
  say "psql not found; installing it..."
  if have brew; then
    brew install libpq >&2
    export PATH="$(brew --prefix)/opt/libpq/bin:$PATH"
  else
    pkg_install postgresql-client postgresql postgresql-client
  fi
  have psql || { say "Installed psql but can't find it on PATH."; exit 1; }
}

# --- credentials and project -------------------------------------------------
ensure_auth() {
  [ -n "${NEON_API_KEY:-}" ] && return
  # `neon auth` on this machine leaves a token the CLI refreshes itself
  # (older CLI versions keep it under neonctl/).
  local c="${XDG_CONFIG_HOME:-$HOME/.config}"
  [ -s "$c/neon/credentials.json" ] || [ -s "$c/neonctl/credentials.json" ] && return
  tty_ok || { say "NEON_API_KEY is not set and there's no terminal to ask for it."; exit 1; }
  say "NEON_API_KEY is not set (and this machine hasn't run \`neon auth\`)."
  say "Create one at https://console.neon.tech/app/settings/api-keys"
  printf 'Neon API key (input hidden): ' >&2
  IFS= read -rs NEON_API_KEY </dev/tty; printf '\n' >&2
  [ -n "$NEON_API_KEY" ] || { say "No key given."; exit 1; }
  export NEON_API_KEY
}

ensure_project() {
  [ -n "${NEON_PROJECT_ID:-}" ] && return
  local linked=""
  if [ -f "$REPO/.neon" ]; then
    linked="$(sed -n 's/.*"projectId"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$REPO/.neon" | head -1)"
  fi
  local default="${linked:-$AGORA_PROJECT_ID}"
  if tty_ok; then
    printf 'NEON_PROJECT_ID is not set. Project [%s]: ' "$default" >&2
    IFS= read -r NEON_PROJECT_ID </dev/tty
  fi
  NEON_PROJECT_ID="${NEON_PROJECT_ID:-$default}"
}

# --- writes on production need a typed confirmation ---------------------------
# Comments are stripped first so "-- DELETE" doesn't count; a keyword inside a
# string literal can still trip it, which only costs a prompt.
is_write() {
  printf '%s\n' "$1" | sed -e 's/--.*$//' | tr '\n' ' ' | sed -e 's:/\*[^*]*\*/::g' |
    grep -qiwE 'insert|update|delete|merge|upsert|drop|alter|truncate|create|grant|revoke|vacuum|reindex|cluster|copy|call|do|refresh|comment|lock'
}

confirm_write() {
  [ "$branch" = "production" ] || return 0
  is_write "$query" || return 0
  [ "$yes" -eq 1 ] && return 0
  tty_ok || { say "This query can change PRODUCTION data. Re-run with --yes to allow it without a prompt."; exit 1; }
  say ""
  say "This query can change data on PRODUCTION (project $NEON_PROJECT_ID):"
  say ""
  printf '%s\n' "$query" | sed 's/^/    /' >&2
  say ""
  printf 'Type "production" to run it: ' >&2
  local answer; IFS= read -r answer </dev/tty
  [ "$answer" = "production" ] || { say "Not run."; exit 1; }
}

ensure_neon
ensure_psql
ensure_auth
ensure_project
confirm_write

say "→ $branch ($NEON_PROJECT_ID)"
# ON_ERROR_STOP + single transaction: the first error rolls everything back.
# The query goes in on stdin (`-f -`, which --single-transaction covers; bare
# stdin isn't), so it can be any size and never needs re-quoting.
printf '%s\n' "$query" |
  "$NEON" psql "$branch" --project-id "$NEON_PROJECT_ID" --no-analytics \
    -- -X -v ON_ERROR_STOP=1 --single-transaction -f -
