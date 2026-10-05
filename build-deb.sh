#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
VERSION=1.9.0
NAME=multiprofile-native
OUT="$ROOT/dist"
# Taken from the control file rather than written twice. The package is
# `Architecture: all` -- it ships Python, a static export and icons, nothing
# compiled -- and a filename that disagrees with the control field produces a
# pool entry nothing downstream can find.
ARCH="$(awk -F': ' '/^Architecture:/{print $2}' packaging/debian/control)"
STAGE="$OUT/${NAME}_${VERSION}_${ARCH}"
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
# App icon: hicolor PNGs at 16..512 plus scalable SVG source.
for _s in 16 32 48 64 128 256 512; do
  mkdir -p "$STAGE/usr/share/icons/hicolor/${_s}x${_s}/apps"
  cp "packaging/icons/hicolor/${_s}x${_s}/apps/multiprofile.png" \
     "$STAGE/usr/share/icons/hicolor/${_s}x${_s}/apps/"
done
mkdir -p "$STAGE/usr/share/icons/hicolor/scalable/apps"
cp packaging/icons/multiprofile.svg "$STAGE/usr/share/icons/hicolor/scalable/apps/"
cp README.md "$STAGE/usr/share/doc/$NAME/"

# The updater (F12). Shipped in the package it updates, so an install that
# predates a security fix still has a way to reach one.
install -Dm0755 packaging/update/multiprofile-update "$STAGE/usr/bin/multiprofile-update"
# The channel config is a template, not a live config: this build is not
# published anywhere yet, and writing a URL that 404s would be worse than
# having none. The postinst does not install it; docs/UPDATES.md says how.
install -Dm0644 packaging/update/update.conf.example \
  "$STAGE/usr/share/doc/$NAME/update.conf.example"

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
DEB="$OUT/${NAME}_${VERSION}_${ARCH}.deb"

# F12: this used to be `dpkg-deb --build` and nothing else, so the script
# could not run on the machine the product is developed on -- Arch has no
# dpkg-deb -- and every packaging change shipped having been built exactly
# once, in a CI job, and never opened. A release process that cannot be
# exercised is one that fails on the day it matters.
#
# dpkg-deb stays the preferred path where it exists, because it is the
# canonical tool. scripts/build_deb.py writes the same three-member ar
# archive from the standard library, so the script now runs anywhere and the
# two are checked against each other in CI.
if command -v dpkg-deb >/dev/null 2>&1; then
  dpkg-deb --build --root-owner-group "$STAGE" "$DEB"
  dpkg-deb -I "$DEB" | head -30
else
  echo "dpkg-deb not found; building with scripts/build_deb.py"
  python3 "$ROOT/scripts/build_deb.py" "$STAGE" "$DEB"
fi

sha256sum "$DEB" | tee "$DEB.sha256"
echo "Built: $DEB"
