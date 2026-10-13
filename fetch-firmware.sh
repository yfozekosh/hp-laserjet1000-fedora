#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Download the HP LaserJet 1000 host-based firmware image.
#
# The firmware is HP-copyrighted, so it is NOT committed to this repo.
# This script fetches it from the mirror that the foo2zjs upstream getweb
# script currently points at (see foo2zjs getweb.in, case "1000").
#
# The image is installed to:
#     /usr/share/foo2zjs/firmware/sihp1000.dl
#
set -euo pipefail

URL="${FIRMWARE_URL:-https://www.quirinux.org/printers/sihp1000.tar.gz}"
DEST="${FIRMWARE_DEST:-/usr/share/foo2zjs/firmware/sihp1000.dl}"
# Known-good sha256 of sihp1000.img as shipped in sihp1000.tar.gz.
EXPECTED_SHA256="6ef9ee2c414dbb3b790f62122b16e952ea7d59b31f82d8699de80f007865677e"

VERIFY=1
[ "${1:-}" = "--no-verify" ] && VERIFY=0

command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Fetching $URL"
curl -fsSL --max-time 120 -o "$WORK/sihp1000.tar.gz" "$URL"

tar xzf "$WORK/sihp1000.tar.gz" -C "$WORK"

IMG="$(find "$WORK" -name 'sihp1000.img' -o -name 'sihp1000.dl' | head -1)"
[ -n "$IMG" ] || { echo "no sihp1000 image inside the tarball" >&2; exit 1; }

SHA="$(sha256sum "$IMG" | cut -d' ' -f1)"
echo "sha256: $SHA"
if [ "$VERIFY" = 1 ] && [ "$SHA" != "$EXPECTED_SHA256" ]; then
    echo "ERROR: unexpected firmware checksum" >&2
    echo "  expected $EXPECTED_SHA256" >&2
    echo "  got      $SHA" >&2
    echo "Re-run with --no-verify if the upstream image has been updated." >&2
    exit 1
fi

# Refuse anything that is not recognisably an HP download image.
case "$(head -c 4 "$IMG" | xxd -p)" in
    444c*)  kind="HP .dl container (ready)" ;;
    beef*)  kind="HP download header (ready)" ;;
    7f454c46*)
        echo "ERROR: got a raw ARM ELF - it needs arm2hpdl first" >&2
        echo "  build tools/arm2hpdl.c, then: arm2hpdl \"$IMG\" > sihp1000.dl" >&2
        exit 1 ;;
    *)
        echo "ERROR: unrecognised firmware header, refusing to install" >&2
        exit 1 ;;
esac
echo "format: $kind"

install -D -m 644 "$IMG" "$DEST"
echo "installed -> $DEST"
echo
echo "Now power-cycle the printer (off -> on); the LED flashes orange for"
echo "about 5 s while the image uploads, or run: sudo hp-lj1000-loadfw"
