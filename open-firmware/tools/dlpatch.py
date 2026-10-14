# SPDX-License-Identifier: GPL-2.0-or-later
#!/usr/bin/env python3
"""Patch the HP LaserJet 1000 firmware image and re-sign it.

The container is two arm2hpdl blobs; only the second (the application ELF) is
patched here, and its trailer checksum is recomputed with the exact algorithm
from tools/arm2hpdl.c:

    check  = sum of little-endian 16-bit words over (sechdr + elf)
    fold to 16 bits, complement
    trailer = ff ff <lo> <hi>

Nothing else in the file is touched, so an unchanged input byte-for-byte
reproduces the known-good image (sha256 6ef9ee2c...677e).

Examples
--------
  read a string back:
      ./dlpatch.py --show 0x0180307e --len 97

  replace the device-ID text (must stay the same length unless --idlen given):
      ./dlpatch.py --va 0x0180307e --str 'MFG:...' --out test.dl

  inject code at a virtual address:
      ./dlpatch.py --va 0x01832760 --bin payload.bin --out test.dl

  structural edit (e.g. bump e_phnum), by ELF-relative offset:
      ./dlpatch.py --off 0x44 --u16 15 --out test.dl
"""
import argparse
import struct
import sys

sys.path.insert(0, sys.argv[0].rsplit('/', 1)[0] or '.')
from fw_map import Image, fold, _fold16  # noqa: E402

SRC = '/usr/share/foo2zjs/firmware/sihp1000.dl'
BLOB2 = 177                 # sechdr of the application blob
ELF0 = 189                  # application ELF
KNOWN_GOOD = '6ef9ee2c414dbb3b790f62122b16e952ea7d59b31f82d8699de80f007865677e'


def resign(raw):
    """Return raw with blob2's trailer recomputed."""
    end = len(raw) - 4
    check = fold(0, raw[BLOB2:ELF0])
    check = fold(check, raw[ELF0:end])
    check = _fold16(check)
    return raw[:end] + b'\xff\xff' + struct.pack('<H', (~check) & 0xffff)


def check_bounds(elf, off, n):
    if off < 0 or off + n > len(elf):
        sys.exit("patch at ELF+0x%x len %d falls outside the ELF (%d bytes)"
                 % (off, n, len(elf)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--src', default=SRC, help='input .dl (default: stock image)')
    ap.add_argument('--out', default=None, help='output .dl (default: stdout-free copy)')
    ap.add_argument('--va', type=lambda s: int(s, 0),
                    help='virtual address to patch (must be file-backed)')
    ap.add_argument('--off', type=lambda s: int(s, 0),
                    help='ELF-relative file offset to patch')
    ap.add_argument('--str', dest='s', help='NUL-terminated ASCII to write')
    ap.add_argument('--bin', help='binary file to write')
    ap.add_argument('--hex', help='hex bytes to write, e.g. eafffffe')
    ap.add_argument('--u16', type=lambda s: int(s, 0), help='write a 16-bit big-endian value')
    ap.add_argument('--u32', type=lambda s: int(s, 0), help='write a 32-bit big-endian value')
    ap.add_argument('--idlen', action='store_true',
                    help='for --str on a device-ID: rewrite the 2-byte IEEE1284 length')
    ap.add_argument('--show', type=lambda s: int(s, 0), metavar='VA',
                    help='print bytes at VA and exit')
    ap.add_argument('--len', dest='n', type=int, default=64,
                    help='byte count for --show')
    ap.add_argument('--verify', action='store_true',
                    help='check the stock image re-signs byte-identically')
    a = ap.parse_args()

    raw = open(a.src, 'rb').read()

    if a.verify:
        out = resign(raw)
        import hashlib
        print("input  %s" % hashlib.sha256(raw).hexdigest())
        print("output %s" % hashlib.sha256(out).hexdigest())
        print("identical: %s" % (out == raw))
        if out == raw:
            print("resign path is provably correct against the stock image")
        return

    img = Image(a.src)
    elf = bytearray(img.elf)

    if a.show is not None:
        b = img.code(a.show, a.n)
        if b is None:
            sys.exit("VA 0x%08x is not file-backed" % a.show)
        print(repr(b.decode('latin-1')))
        return

    # ---- work out what to write and where ----
    data = None
    if a.s is not None:
        data = a.s.encode() + b'\x00'
    elif a.bin:
        data = open(a.bin, 'rb').read()
    elif a.hex:
        h = a.hex.replace(' ', '').replace('_', '')
        data = bytes.fromhex(h)
    elif a.u32 is not None:
        data = struct.pack('>I', a.u32)
    elif a.u16 is not None:
        data = struct.pack('>H', a.u16)
    if data is None:
        sys.exit("nothing to do: pass --show/--verify or one of "
                 "--str/--bin/--hex/--u16/--u32")

    if a.va is not None:
        off = img.va2off(a.va)
        if off is None:
            sys.exit("VA 0x%08x is not file-backed (BSS/NOBITS?)" % a.va)
        va, label = a.va, 'va 0x%08x' % a.va
    elif a.off is not None:
        off, va, label = a.off, None, 'elf+0x%x' % a.off
    else:
        sys.exit("pass --va or --off")

    check_bounds(elf, off, len(data))

    # IEEE 1284 device ID: 2-byte big-endian length *including* itself.
    if a.idlen and a.va is not None:
        n = len(data) - 1
        idstart = img.va2off(a.va - 2)
        if idstart is None:
            sys.exit("--idlen needs --va to point just after the 2-byte length")
        struct.pack_into('>H', elf, idstart, n + 2)
        print("device-ID length field -> %d (2 + %d)" % (n + 2, n))

    old = bytes(elf[off:off + len(data)])
    elf[off:off + len(data)] = data
    print("patched %s  ELF+0x%x  %d bytes" % (label, off, len(data)))
    print("  old: %s" % old.hex(' '))
    print("  new: %s" % data.hex(' '))

    # ---- reassemble ----
    out = raw[:ELF0] + bytes(elf) + raw[len(img.raw) - 4:]
    if len(elf) != len(img.elf):
        sys.exit("ELF length changed; refusing (move sections/trailer by hand)")
    out = resign(out)

    if a.out:
        open(a.out, 'wb').write(out)
        import hashlib
        print("wrote %s (%d bytes)" % (a.out, len(out)))
        print("  sha256 %s" % hashlib.sha256(out).hexdigest())
    else:
        sys.stdout.buffer.write(out)


if __name__ == '__main__':
    main()
