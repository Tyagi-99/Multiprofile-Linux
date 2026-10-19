#!/usr/bin/env bash
# Take a verified whole-store backup. Safe to run unattended.
#
# This is the procedure docs/BACKUP_AND_RESTORE.md §1 describes, as a script,
# so the scheduled path and the documented path cannot drift apart.
#
# THE RUNNING-PROFILE PROBLEM
#
# `chrome-profiles/` is written continuously by every live Chromium, so
# copying it under a running profile can capture a torn cookie jar or a
# half-written LevelDB. The manual procedure says "stop the application
# first". A timer cannot do that, and nothing in this product auto-parks an
# idle profile, so an operator who leaves profiles open would otherwise get
# no backups at all -- silently, which is the worst outcome of the three.
#
# So this takes one of two kinds and says which:
#
#   full        nothing is running. Everything, including browser data.
#   metadata    something is running. The control database and the config:
#               profile definitions, identity seeds, proxy settings,
#               extension assignments, workspaces. NOT cookies or sessions.
#
# A metadata backup is not a consolation prize -- the seeds are the identity,
# and a profile restored from one presents the same browser. What it cannot
# give back is logged-in sessions. The MANIFEST in each backup records which
# kind it is so a restore is never a surprise.
set -euo pipefail

KEEP=${KEEP:-7}
DEST_ROOT=${MULTIPROFILE_BACKUP_DIR:-$HOME/multiprofile-backups}
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA_DIR="$DATA_HOME/multiprofile"
DB="$DATA_DIR/control.db"
FORCE_METADATA=0
QUIET=0

while [ $# -gt 0 ]; do
  case "$1" in
    --keep) KEEP=$2; shift 2 ;;
    --dest) DEST_ROOT=$2; shift 2 ;;
    --metadata-only) FORCE_METADATA=1; shift ;;
    --quiet) QUIET=1; shift ;;
    -h|--help)
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
      echo
      echo "Usage: $0 [--keep N] [--dest DIR] [--metadata-only] [--quiet]"
      exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say() { [ "$QUIET" = 1 ] || printf '%s\n' "$*"; }
fail() { printf 'backup failed: %s\n' "$*" >&2; exit 1; }

[ -f "$DB" ] || fail "no control database at $DB — is multiprofile installed and used yet?"
command -v sqlite3 >/dev/null 2>&1 || fail "sqlite3 is not installed"

# --- which kind can we honestly take? ---------------------------------------
running=""
if [ "$FORCE_METADATA" = 0 ]; then
  # profiles_runtime is the authoritative store. A row can outlive its process,
  # so each pid is checked rather than trusted.
  while IFS='|' read -r pid_profile pid; do
    [ -n "${pid:-}" ] || continue
    if kill -0 "$pid" 2>/dev/null; then
      running="$running $pid_profile"
    fi
  done <<EOF
$(sqlite3 "$DB" "SELECT profile_id, pid FROM profiles_runtime WHERE state='running' AND pid IS NOT NULL;" 2>/dev/null || true)
EOF
fi

if [ "$FORCE_METADATA" = 1 ]; then
  KIND=metadata
  WHY="--metadata-only was requested"
elif [ -n "$running" ]; then
  KIND=metadata
  WHY="these profiles are running, so their browser data cannot be copied consistently:$running"
else
  KIND=full
  WHY="nothing is running"
fi

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST="$DEST_ROOT/$STAMP"
mkdir -p "$DEST"
trap 'rm -rf "$DEST"' EXIT   # a half-written backup must not look like a backup

say "backup: $DEST"
say "kind:   $KIND ($WHY)"

# --- take it ----------------------------------------------------------------
# SQLite's own backup API: consistent even with the application running.
sqlite3 "$DB" ".backup '$DEST/control.db'" || fail "could not copy the control database"
tar -C "$CONFIG_HOME" -czf "$DEST/config.tar.gz" multiprofile \
  || fail "could not archive the configuration"

# Caches are excluded from both kinds. The names come from
# `backup._SKIP_DIR_NAMES` -- the product's own judgement about what is not
# identity and is left out of a per-profile backup -- so there is one
# definition of "this is a cache" rather than two that can drift. They are
# large, they rebuild themselves, and they are not what anyone restores for.
# On this machine the difference was 510M -> 95M.
CACHE_EXCLUDES=(
  --exclude='Cache' --exclude='Code Cache' --exclude='GPUCache'
  --exclude='GPUPersistentCache' --exclude='Service Worker'
  --exclude='ShaderCache' --exclude='GrShaderCache'
  --exclude='DawnCache' --exclude='DawnGraphiteCache' --exclude='DawnWebGPUCache'
)

if [ "$KIND" = full ]; then
  tar -C "$DATA_HOME" "${CACHE_EXCLUDES[@]}" -czf "$DEST/data.tar.gz" multiprofile \
    || fail "could not archive the data directory"
else
  # Everything except the live browser directories.
  tar -C "$DATA_HOME" "${CACHE_EXCLUDES[@]}" -czf "$DEST/data.tar.gz" \
      --exclude='multiprofile/chrome-profiles' \
      --exclude='multiprofile/webview' \
      multiprofile || fail "could not archive the data directory"
fi

( cd "$DEST" && sha256sum control.db data.tar.gz config.tar.gz > SHA256SUMS )

# --- verify it, because an unverified backup is a guess ---------------------
( cd "$DEST" && sha256sum -c --quiet SHA256SUMS ) || fail "checksums do not match"
integrity=$(sqlite3 "$DEST/control.db" "PRAGMA integrity_check;")
[ "$integrity" = "ok" ] || fail "the copied database failed integrity_check: $integrity"
profiles=$(sqlite3 "$DEST/control.db" "SELECT COUNT(*) FROM profiles;")
schema=$(sqlite3 "$DEST/control.db" "SELECT MAX(version) FROM schema_version;")

cat > "$DEST/MANIFEST" <<EOF
tool: multiprofile scripts/backup.sh
kind: $KIND
reason: $WHY
taken_at: $STAMP
schema_version: $schema
profiles: $profiles
source_data_dir: $DATA_DIR
contains_browser_data: $([ "$KIND" = full ] && echo yes || echo "no — cookies and logged-in sessions are NOT in this backup")
verified: sha256 + PRAGMA integrity_check
EOF

trap - EXIT
size=$(du -sh "$DEST" | cut -f1)
say "verified: integrity=ok profiles=$profiles schema=v$schema size=$size"

# --- retention --------------------------------------------------------------
# Only directories this script made are ever removed. Anything an operator put
# here by hand has no MANIFEST and is left alone, whatever its name.
if [ "$KEEP" -gt 0 ] 2>/dev/null; then
  mapfile -t ours < <(
    find "$DEST_ROOT" -mindepth 2 -maxdepth 2 -name MANIFEST -printf '%h\n' 2>/dev/null \
      | xargs -r -I{} sh -c 'grep -q "^tool: multiprofile scripts/backup.sh$" "{}/MANIFEST" && echo "{}"' \
      | sort
  )
  count=${#ours[@]}
  if [ "$count" -gt "$KEEP" ]; then
    drop=$(( count - KEEP ))
    for old in "${ours[@]:0:$drop}"; do
      say "pruning $old"
      rm -rf "$old"
    done
  fi
  say "kept $(( count > KEEP ? KEEP : count )) of $count automatic backups (KEEP=$KEEP)"
fi

if [ "$KIND" = metadata ]; then
  say ""
  say "NOTE: this is a metadata backup. Profile definitions, identity seeds,"
  say "      proxy settings and extension assignments are in it; cookies and"
  say "      logged-in sessions are not. Stop your profiles and run this again"
  say "      for a full one."
fi
