# SPDX-License-Identifier: GPL-2.0-or-later
#!/usr/bin/env python3
"""Prepare the LJ1000 image for radare2/Ghidra.

The download image is wrapped in an HP 0xBEEF/0xC0DE envelope with a second
(stub) ELF in front of the real one, so tools that want a plain ELF refuse it.

Both checksummed blobs are exactly reproducible (see fw_map.py), so the only
transformation needed is: extract the inner ELF.  Its section headers, symbol
table and string table are intact and are kept -- radare2 imports `.symtab`
natively, which gives `sym.*` / `obj.*` flags and lets `aaa` name every
function correctly.  Pre-seeding functions with `af+` instead *breaks* the
analysis (size-0 functions are never re-analysed), so this script only emits
what the ELF does not carry: the MMIO map recovered from .rodata pointers.

Outputs:  lj1000.elf   lj1000.r2cmds   symbols.json

Usage:
    r2 -i lj1000.r2cmds -c 'aaa; pdf @sym.main; axt @sym.usbReadPIO' lj1000.elf
"""
import json
import sys

sys.path.insert(0, sys.argv[0].rsplit('/', 1)[0] or '.')
from fw_map import Image  # noqa: E402

SRC = '/usr/share/foo2zjs/firmware/sihp1000.dl'
OUT = 'lj1000.elf'
CMDS = 'lj1000.r2cmds'

# Peripheral blocks recovered by dereferencing .rodata pointer variables
# (see usb_regs.py).  Offsets are what the firmware actually touches.
FLOORS = (
    (0x01010000, 0x100, 'usb',   'USB controller'),
    (0x01020000, 0x100, 'img',   'image / JBIG / video'),
    (0x01025000, 0x100, 'jbig',  'JBIG'),
    (0x0102a000, 0x100, 'vid',   'video'),
    (0x01030000, 0x100, 'irq',   'interrupt controller'),
    (0x01040000, 0x100, 'misc',  'misc / tx-thread-interrupt-control'),
    (0x01044040, 0x4,   'pjlout', 'zjPjlOutput'),
)

# Named registers confirmed by code inspection (symbol -> offset in its block).
REGS = (
    (0x01010000, 0x04, 'usb_ctrl'),
    (0x01010000, 0x0c, 'usb_ctrl2'),
    (0x01010000, 0x40, 'usb_ack0'),
    (0x01010000, 0x44, 'usb_ack1'),
    (0x01010000, 0x80, 'usb_pio_data'),      # bit15 = ready
    (0x01010000, 0x84, 'usb_pio_ctrl'),
    (0x01010000, 0x88, 'usb_pio_status'),
    (0x01030000, 0x0c, 'irq_enable'),
    (0x01030000, 0x4c, 'irq_status'),        # bits 8..15
    (0x01030000, 0x64, 'irq_set'),           # boot stub writes 0x33 here
)


def main():
    img = Image(SRC)

    with open(OUT, 'wb') as fh:
        fh.write(img.elf)
    print("wrote %s (%d bytes)  section headers preserved" % (OUT, len(img.elf)))

    funcs = img.symbols('func')
    objs = img.symbols('object')

    with open(CMDS, 'w') as fh:
        fh.write("# r2 commands -- supplementary flags only.\n")
        fh.write("# radare2 reads the ELF .symtab itself, so run:\n")
        fh.write("#   r2 -i lj1000.r2cmds -c 'aaa; pdf @sym.main' lj1000.elf\n")
        fh.write("# (do NOT pre-seed functions with af+: size-0 functions\n")
        fh.write("#  are never analysed by aaa.)\n\n")

        fh.write("fs peripherals\n")
        for base, span, tag, desc in FLOORS:
            fh.write("f %s_base = %d @ 0x%08x   # %s\n" % (tag, span, base, desc))
            for off in range(0, span, 4):
                fh.write("f %s_%04x = 4 @ 0x%08x\n" % (tag, off, base + off))
        for addr, span, name in REGS:
            fh.write("f reg_%s = %d @ 0x%08x\n" % (name, span, addr))

        fh.write("\nfs landmarks\n")
        for name in ('__ghs_ind_crt0', 'main', 'StartupShutdown', '__return',
                     'usbDrvInit', 'usbReadPIO', 'usbWrite', 'usbIsrCtrl',
                     'engISR', 'zjThreadMaster'):
            s = img._byname.get(name)
            if s:
                fh.write("f lm_%s = 4 @ 0x%08x\n" % (name, s[0]['addr']))

        fh.write("\nfs sections\n")
        for s in img.secs:
            if s['type'] in (0, 8) or not s['size']:
                continue
            nm = img.secname(s['idx']).replace('.', '_')
            fh.write("f sect_%s = %d @ 0x%08x\n" % (nm, s['size'], s['addr']))

    print("wrote %s (%d funcs, %d objects in the ELF; MMIO + landmarks + sections)"
          % (CMDS, len(funcs), len(objs)))

    with open('symbols.json', 'w') as fh:
        json.dump({'sections': [dict(s, name=img.secname(s['idx']))
                                for s in img.secs],
                   'segments': img.segs,
                   'symbols': img.symbols()}, fh, indent=1)
    print("wrote symbols.json (%d symbols)" % len(img.symbols()))


if __name__ == '__main__':
    main()
