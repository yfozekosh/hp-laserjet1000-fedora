#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Remove everything install.sh put in place.
#
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo ./uninstall.sh" >&2; exit 1; }

rm -f /usr/local/sbin/hp-lj1000-loadfw
rm -f /etc/udev/rules.d/99-hp-lj1000-firmware.rules
rm -f /etc/systemd/system/hp-lj1000-loadfw.service

systemctl daemon-reload
udevadm control --reload-rules

echo "Removed loader, udev rule and systemd unit."
echo "(The firmware image in /usr/share/foo2zjs/firmware/ was left alone.)"
