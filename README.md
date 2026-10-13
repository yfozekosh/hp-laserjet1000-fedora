# HP LaserJet 1000 on Fedora

Everything needed to make an **HP LaserJet 1000** (`03f0:0517`) print on a
modern Fedora/CUPS system with the `foo2zjs` driver — including the piece
that almost nobody tells you about.

---

## The problem

Jobs queue up and then fail with:

```
Got USB pipe stalled during read/write
libusb write operation returned fffffff7          # -9, LIBUSB_ERROR_PIPE
Sent 0 bytes
```

Meanwhile the printer:

* enumerates fine on USB,
* answers `GET_DEVICE_ID` fine,
* shows up correctly in CUPS as `HP LaserJet 1000 Foomatic/foo2zjs`,
* and silently swallows every job.

You can chase this for a long time. I did — I chased it as a bad cable,
a marginal signal (`error -71` / EPROTO in the kernel log), a driver
mismatch, and a timing problem. **None of those were the cause.**

## Root cause

The LaserJet 1000 is a **host-based** printer. It keeps **no engine
firmware in ROM** — the firmware image has to be re-uploaded over USB
**every time the printer is powered on**.

The tell is the device ID string. Compare:

```text
before:  MFG:Hewlett-Packard;MDL:hp LaserJet 1000;CMD:ZJS;CLS:PRINTER;DES:hp LaserJet 1000;
after:   MFG:Hewlett-Packard;MDL:hp LaserJet 1000;CMD:ZJS;CLS:PRINTER;DES:hp LaserJet 1000;FWVER:20011207;
```

**No `FWVER:` = no firmware = dead print engine.**

The USB microcontroller has its *own* ROM, which is why the printer looks
perfectly alive. The print engine, however, never wakes up — so every bulk
OUT transfer stalls with `EPIPE`.

The moment firmware was uploaded, the stall rate went from ~50% of packets
to **zero**.

## Why the usual tools don't fix it

| Tool | What happens |
|---|---|
| CUPS `usb` backend | No stall recovery — first `EPIPE` aborts, `Sent 0 bytes` |
| HPLIP `hp` backend | Hangs at startup, never completes the job |
| `foo2zjs` driver | **Not the problem.** Rendering is correct; only transport fails |

`foo2zjs` with `-P -r600x600` is the right driver. No need to change it.

---

## Install

```bash
sudo ./install.sh          # loader, udev rule, systemd unit
sudo ./fetch-firmware.sh   # download the HP firmware image (not in git)
```

Then power-cycle the printer (off → on). The LED flashes orange for about
5 seconds while the image uploads.

---

## Verify

```bash
lsusb -d 03f0:0517
sudo /usr/lib/cups/backend/usb | grep -o 'FWVER:[0-9]*'
#   FWVER:20011207

sudo hp-lj1000-loadfw --force   # manual re-upload + full log
journalctl -u hp-lj1000-loadfw.service -b --no-pager
```

Manual one-shot, without systemd:

```bash
sudo hp-lj1000-loadfw
```

---

## What's here

| File | Purpose |
|---|---|
| `hp-lj1000-loadfw` | Firmware uploader (Python, usbfs, `CLEAR_HALT` stall recovery) |
| `99-hp-lj1000-firmware.rules` | udev rule — fires on printer plug-in / power-on |
| `hp-lj1000-loadfw.service` | oneshot unit that runs the uploader after a 3 s settle |
| `fetch-firmware.sh` | downloads + checksums + installs `sihp1000.dl` |
| `install.sh` / `uninstall.sh` | put it all in place / take it all out |
| `tools/arm2hpdl.c` | optional; wraps a raw ARM ELF in the HP download header |

### How it works

```
printer plugged in / powered on
        │
        ▼
udev  ACTION=="add"  idVendor==03f0  idProduct==0517
        │  TAG+="systemd", SYSTEMD_WANTS+=…
        ▼
systemd  hp-lj1000-loadfw.service   (sleep 3, then run)
        │
        ▼
hp-lj1000-loadfw
  ├─ skip if the device already reports FWVER:  (unless --force)
  ├─ unbind usblp so we can claim the interface
  ├─ USBDEVFS_RESET, claim interface 0, CLEAR_HALT both endpoints
  ├─ write 114 KB in 64-byte chunks
  │     └─ on EPIPE: CLEAR_HALT and retry  ← this is the key part
  └─ rebind usblp, print the new device ID
```

---

## Troubleshooting

**`ERROR: printer not found on USB`**
Check `lsusb -d 03f0:0517`. The device must be plugged in *and* powered on.

**`ERROR: … consecutive stalls at offset N`**
The image is not getting through. Power-cycle the printer and retry — the
printer holds no writable engine flash, so a failed upload is always
recoverable by simply power-cycling. If it persists, check the cable and
that nothing else has claimed the device (`sudo lsof /dev/bus/usb/*/*`).

**`ERROR: unexpected firmware checksum`**
The upstream mirror changed. Inspect the new image; if it is genuinely a
newer firmware, update `EXPECTED_SHA256` or pass `--no-verify`.

**Firmware loaded but jobs still fail**
Confirm the queue is pointed at the plain CUPS backend, not HPLIP's:

```bash
lpstat -v
# device for hp-LaserJet-1000: usb://HP/LaserJet%201000?serial=0.0

lpadmin -p hp-LaserJet-1000 -E -v 'usb://HP/LaserJet%201000?serial=0.0' \
        -P /usr/share/ppd/foo2zjs/*.ppd     # if it needs redoing
```

**Queue stuck in `Stopped`**

```bash
sudo cupsenable hp-LaserJet-1000
sudo cupsaccept  hp-LaserJet-1000
sudo cancel -a   hp-LaserJet-1000
```

---

## Uninstall

```bash
sudo ./uninstall.sh
```

---

## License

**GPL-2.0-or-later** — see [`LICENSE`](LICENSE).

This matches [foo2zjs](https://www.quirinux.org/printers/), which this repo
complements and from which `tools/arm2hpdl.c` is vendored verbatim.

```bash
# SPDX-License-Identifier: GPL-2.0-or-later
```

The HP firmware image itself is **not** covered by this license: it is
HP-copyrighted and is deliberately excluded from the repository by
`.gitignore`. `fetch-firmware.sh` obtains it at install time; you may
redistribute it only as HP permits.

---

## Notes

* **This cannot brick the printer.** There is no writable engine flash to
  corrupt. The ROM bootloader accepts the download on every power cycle, so
  a failed or interrupted upload just means you power-cycle and try again.
* **Firmware is not committed to this repo.** `sihp1000.img` is
  HP-copyrighted; `.gitignore` excludes it and `fetch-firmware.sh` pulls it
  from the mirror the upstream `foo2zjs` `getweb` script points at.
* The image shipped in that tarball **already carries the HP download
  header** (`0xBE EF "ABCDEFGH"`), so `arm2hpdl` passes it through
  unchanged. You only need `tools/arm2hpdl.c` if you start from a raw ARM
  ELF such as the one inside HP's own `lj1488en.exe`.
* Driver: **`foo2zjs`**, invoked as
  `gs … -sDEVICE=pbmraw -r600x600 | foo2zjs -r600x600 -g5100x6600 -p1 -m1 -s7 -z0 -P`.

Tested on Fedora 44, kernel `xhci_hcd`, CUPS + `foo2zjs`.
