#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
VERSION=1.4.0
NAME=multiprofile-native
OUT="$ROOT/dist"
STAGE="$OUT/${NAME}_${VERSION}_amd64"
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" \
  "$STAGE/usr/bin" \
  "$STAGE/usr/lib/multiprofile" \
  "$STAGE/usr/share/multiprofile" \
  "$STAGE/usr/share/applications" \
  "$STAGE/usr/share/doc/$NAME"

cp -a src/multiprofile "$STAGE/usr/lib/multiprofile/"
cp -a src/static "$STAGE/usr/share/multiprofile/"
cp -a src/extension "$STAGE/usr/share/multiprofile/"
cp packaging/debian/control "$STAGE/DEBIAN/control"
cp packaging/debian/postinst "$STAGE/DEBIAN/postinst"
chmod 0755 "$STAGE/DEBIAN/postinst"
cp packaging/debian/changelog "$STAGE/usr/share/doc/$NAME/"
cp packaging/debian/copyright "$STAGE/usr/share/doc/$NAME/"
cp packaging/debian/multiprofile.desktop "$STAGE/usr/share/applications/multiprofile-native.desktop"
cp README.md "$STAGE/usr/share/doc/$NAME/"

cat > "$STAGE/usr/bin/multiprofile" <<'PY'
#!/usr/bin/env python3
import os, sys
sys.path.insert(0, "/usr/lib/multiprofile")
os.environ.setdefault("MULTIPROFILE_STATIC", "/usr/share/multiprofile/static")
os.environ.setdefault("MULTIPROFILE_EXTENSION", "/usr/share/multiprofile/extension")
from multiprofile.__main__ import main
main()
PY
chmod 0755 "$STAGE/usr/bin/multiprofile"

SIZE=$(du -sk "$STAGE" | cut -f1)
if ! grep -q '^Installed-Size:' "$STAGE/DEBIAN/control"; then
  sed -i "s/^Description:/Installed-Size: ${SIZE}\nDescription:/" "$STAGE/DEBIAN/control"
fi

mkdir -p "$OUT"
dpkg-deb --build "$STAGE" "$OUT/${NAME}_${VERSION}_amd64.deb"
dpkg-deb -I "$OUT/${NAME}_${VERSION}_amd64.deb" | head -30
echo "Built: $OUT/${NAME}_${VERSION}_amd64.deb"
