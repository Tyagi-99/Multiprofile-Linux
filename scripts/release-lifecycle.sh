#!/usr/bin/env bash
# Run the package lifecycle in a clean Debian container.
#
# A thin wrapper: the work is in release-lifecycle-inner.sh, which CI calls
# directly because its job already runs in a container. Use this one on a
# developer machine, where installing and removing system packages to test a
# release process is not something to do to your own root filesystem.
#
#     bash scripts/release-lifecycle.sh
#
# Needs docker or podman. On a machine where the user is not in the `docker`
# group this needs sudo, which is why it is a script you run rather than
# something the test suite does for you.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${IMAGE:-debian:bookworm-slim}"
RUNNER="${RUNNER:-docker}"

command -v "$RUNNER" >/dev/null || {
  echo "error: $RUNNER not found. Set RUNNER=podman, or install docker." >&2
  exit 1
}

echo "== package lifecycle in $IMAGE =="
exec "$RUNNER" run --rm -v "$ROOT:/src:ro" -w /work "$IMAGE" bash -euo pipefail -c '
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends \
    python3 python3-flask python3-websocket ca-certificates \
    gnupg gpgv zstd dpkg-dev binutils file >/dev/null
  cp -a /src /work/src
  bash /work/src/scripts/release-lifecycle-inner.sh
'
