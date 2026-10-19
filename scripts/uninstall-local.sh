#!/usr/bin/env bash
# Remove a rootless install. Never touches the operator's data.
set -euo pipefail

PREFIX=${PREFIX:-$HOME/.local}
APP_DIR=${APP_DIR:-$PREFIX/lib/multiprofile}
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/multiprofile"

# The timer first: a unit left behind pointing at a removed script would fail
# every day in the operator's journal.
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user disable --now multiprofile-backup.timer >/dev/null 2>&1 || true
fi
rm -f "$UNIT_DIR/multiprofile-backup.timer" "$UNIT_DIR/multiprofile-backup.service"
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload >/dev/null 2>&1 || true
fi

rm -rf "$APP_DIR"
rm -f  "$PREFIX/bin/multiprofile"
rm -f  "$PREFIX/bin/multiprofile-backup"
rm -f  "$PREFIX/share/applications/multiprofile.desktop"
for size in 16x16 32x32 48x48 64x64 128x128 256x256 512x512; do
  rm -f "$PREFIX/share/icons/hicolor/$size/apps/multiprofile.png"
done

cat <<EOF
Removed the application.

Your profiles, cookies and control database are still at:
  $DATA_DIR

Your backups are still at:
  ${MULTIPROFILE_BACKUP_DIR:-$HOME/multiprofile-backups}

Delete that directory yourself if you really want it gone. An uninstaller
that removes an operator's profiles because they upgraded is not a trade
this script will make for you.
EOF
