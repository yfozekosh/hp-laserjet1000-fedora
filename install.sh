#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Install the HP LaserJet 1000 firmware loader, udev rule, systemd unit
# and (optionally) the toner-boost renderer.
#
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo ./install.sh" >&2; exit 1; }

OPT_RENDER=1
for a in "$@"; do
    case "$a" in
    --no-render) OPT_RENDER=0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
    esac
done

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

# ----------------------------------------------------------------------
# Optional: toner boost.
#
# foomatic-rip refuses FoomaticRIPCommandLine values that are not in its
# sha256 allowlist (/etc/foomatic/hashes.d), so after editing a PPD we
# must re-run foomatic-hash(1) -- the documented way to bless the value.
# ----------------------------------------------------------------------
if [ "$OPT_RENDER" -eq 1 ]; then
    echo
    echo "==> toner-boost renderer -> /usr/local/bin/hp-lj1000-render"
    install -m 755 "$here/hp-lj1000-render" /usr/local/bin/hp-lj1000-render

    if [ ! -f /etc/hp-lj1000-toner ]; then
        echo "==> toner factor -> /etc/hp-lj1000-toner (0.75)"
        echo '0.75' > /etc/hp-lj1000-toner
        chmod 644 /etc/hp-lj1000-toner
    else
        echo "    /etc/hp-lj1000-toner exists, leaving it alone"
    fi

    patched=0
    for ppd in /etc/cups/ppd/*.ppd; do
        [ -f "$ppd" ] || continue
        grep -q 'FoomaticRIPCommandLine: "foo2zjs-wrapper %A"' "$ppd" || continue

        name="$(basename "$ppd" .ppd)"
        echo "==> PPD $name: point renderer at hp-lj1000-render"
        [ -f "$ppd.bak" ] || cp -a "$ppd" "$ppd.bak"

        sed -i 's|FoomaticRIPCommandLine: "foo2zjs-wrapper %A"|FoomaticRIPCommandLine: "/usr/local/bin/hp-lj1000-render %A"|' "$ppd"

        if command -v foomatic-hash >/dev/null 2>&1; then
            echo "    blessing the new command line with foomatic-hash"
            foomatic-hash --ppd "$ppd" \
                "/tmp/foomatic-review-$name.txt" \
                "/etc/foomatic/hashes.d/hashes.$name"
        else
            echo "    !! foomatic-hash not found - the queue will reject the"
            echo "       new command line.  Install it and re-run."
        fi
        patched=$((patched + 1))
    done

    if [ "$patched" -eq 0 ]; then
        echo "    no unpatched foo2zjs PPD found (already converted, or none present)"
    fi
fi

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
if [ "$OPT_RENDER" -eq 1 ]; then
    echo "  cat /etc/hp-lj1000-toner       # toner factor (1.0 disables)"
fi
