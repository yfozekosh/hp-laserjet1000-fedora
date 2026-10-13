#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Install the HP LaserJet 1000 firmware loader, udev rule and systemd unit.
#
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo ./install.sh" >&2; exit 1; }

echo "==> firmware loader -> /usr/local/sbin/hp-lj1000-loadfw"
install -m 755 "$here/hp-lj1000-loadfw" /usr/local/sbin/hp-lj1000-loadfw

echo "==> udev rule -> /etc/udev/rules.d/99-hp-lj1000-firmware.rules"
install -m 644 "$here/99-hp-lj1000-firmware.rules" \
    /etc/udev/rules.d/99-hp-lj1000-firmware.rules

echo "==> systemd unit -> /etc/systemd/system/hp-lj1000-loadfw.service"
install -m 644 "$here/hp-lj1000-loadfw.service" \
    /etc/systemd/system/hp-lj1000-loadfw.service

systemctl daemon-reload
udevadm control --reload-rules
echo "    udev rules reloaded"

if [ ! -f /usr/share/foo2zjs/firmware/sihp1000.dl ]; then
    echo
    echo "!! firmware image not present."
    echo "   Run: sudo ./fetch-firmware.sh"
fi

echo
echo "Done. Verify with:"
echo "  sudo udevadm verify /etc/udev/rules.d/99-hp-lj1000-firmware.rules"
echo "  sudo hp-lj1000-loadfw          # manual upload"
echo "  lsusb -d 03f0:0517             # device present?"
