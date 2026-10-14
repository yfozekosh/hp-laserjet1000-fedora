# SPDX-License-Identifier: GPL-2.0-or-later
#!/usr/bin/env python3
"""Extract the MMIO register map used by the LJ1000 USB driver.

ARM loads 32-bit peripheral addresses out of literal pools:

    ldr  r0, [pc, #0x5c]      ; target = (pc_of_insn + 8) aligned, + offset

So walking every `ldr rd, [pc, #N]` inside a function and reading the word it
points at yields the absolute address the function touches.  Addresses outside
the code/RAM window (0x01800000..0x01840000) are peripheral registers.

Usage: usb_regs.py [symbol-substring ...]
"""
import struct
import sys

from fw_map import Image

FW = '/usr/share/foo2zjs/firmware/sihp1000.dl'
CODE_LO, CODE_HI = 0x01800000, 0x01840000


def literals(img, va, size):
    """Yield (insn_addr, resolved_target_value)."""
    blob = img.code(va, size)
    if not blob:
        return
    for ins in img.md.disasm(blob, va):
        if ins.mnemonic != 'ldr':
            continue
        op = ins.op_str
        if '[pc' not in op or '#' not in op:
            continue
        try:
            imm = int(op.rsplit('#', 1)[1].split(']')[0], 0)
        except ValueError:
            continue
        pool = ((ins.address + 8) & ~3) + imm
        off = img.va2off(pool)
        if off is None or off + 4 > len(img.elf):
            continue
        yield ins.address, pool, struct.unpack(
            '>I', img.elf[off:off + 4])[0]


def main(needles):
    img = Image(FW)
    syms = {s['name']: s for s in img.symbols()}
    if not needles:
        needles = ['usb', 'channel', 'pod']

    hits = {}
    for name, s in sorted(syms.items()):
        if not any(n in name for n in needles):
            continue
        blob = img.code(s['addr'], s['size'])
        if not blob:
            continue
        found = []
        for addr, pool, val in literals(img, s['addr'], s['size']):
            found.append((addr, pool, val))
        if found:
            hits[name] = (s, found)

    print("=== literal-pool addresses referenced by each function ===\n")
    mmio = {}
    for name, (s, found) in hits.items():
        marks = []
        for addr, pool, val in found:
            if CODE_LO <= val < CODE_HI:
                continue          # code/data reference, not hardware
            mmio.setdefault(val, set()).add(name)
            marks.append("0x%08x@0x%08x" % (val, addr))
        print("  %-26s 0x%08x sz=%-5d %s"
              % (name, s['addr'], s['size'], ' '.join(marks)))

    print("\n=== peripheral addresses outside code/RAM window ===")
    print("  %-12s %s" % ("address", "used by"))
    for a in sorted(mmio):
        print("  0x%08x    %s" % (a, ', '.join(sorted(mmio[a]))))

    print("\n=== candidate register block(s) ===")
    if mmio:
        lo, hi = min(mmio), max(mmio)
        print("  span 0x%08x .. 0x%08x  (%d distinct addresses,"
              " range 0x%x = %d KiB)"
              % (lo, hi, len(mmio), hi - lo + 4, (hi - lo + 4) // 1024))
        # cluster addresses that are close together (same block)
        addrs = sorted(mmio)
        blocks, cur = [], [addrs[0]]
        for a in addrs[1:]:
            if a - cur[-1] <= 0x100:
                cur.append(a)
            else:
                blocks.append(cur); cur = [a]
        blocks.append(cur)
        for b in blocks:
            print("  block 0x%08x..0x%08x  %d regs"
                  % (b[0], b[-1], len(b)))


if __name__ == '__main__':
    main(sys.argv[1:])
