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

# Toner boost: put the PPDs back the way they were.
for ppd in /etc/cups/ppd/*.ppd; do
    [ -f "$ppd" ] || continue
    if grep -q 'FoomaticRIPCommandLine: "/usr/local/bin/hp-lj1000-render %A"' "$ppd"; then
        if [ -f "$ppd.bak" ]; then
            echo "==> restoring $(basename "$ppd") from $ppd.bak"
            cp -a "$ppd.bak" "$ppd"
        else
            echo "==> reverting $(basename "$ppd") in place"
            sed -i 's|FoomaticRIPCommandLine: "/usr/local/bin/hp-lj1000-render %A"|FoomaticRIPCommandLine: "foo2zjs-wrapper %A"|' "$ppd"
        fi
    fi
done

rm -f /usr/local/bin/hp-lj1000-render
rm -f /etc/hp-lj1000-toner

echo "Removed loader, udev rule, systemd unit and toner-boost renderer."
echo "(The firmware image in /usr/share/foo2zjs/firmware/ was left alone.)"
