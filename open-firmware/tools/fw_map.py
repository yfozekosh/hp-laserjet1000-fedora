# SPDX-License-Identifier: GPL-2.0-or-later
#!/usr/bin/env python3
"""Map the HP LaserJet 1000 firmware image.

Container layout (verified against arm2hpdl.c, see tools/arm2hpdl.c):

    [0..17)         filhdr  beef "ABCDEFGH" size=st_size+16 000000
    [17..29)        sechdr  c0de "ABCDEFGH" 0000
    [29..173)       outer stub ELF (144 B) -- does *(uint32*)0x01030064 = 0x33; bx lr
    [173..177)      trailer ff ff ~check        (verify: 0x7498)
    [177..189)      sechdr  c0de "ABCDEFGH" 0000
    [189..114229)   inner application ELF (114040 B = 0x1bd78)
    [114229..)      trailer ff ff ~check        (verify: 0x14d8)

    check = fold16(sum of little-endian 16-bit words of (sechdr + payload));
    trailer holds ~check, low byte first.

The inner ELF is *not* stripped: its section table (27 entries), .symtab
(705 symbols) and .strtab are all intact, so no heuristic recovery is needed.

Usage: fw_map.py <sihp1000.dl>
"""
import struct
import sys
import re as _re
from collections import Counter

from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_BIG_ENDIAN

SHT = {0: 'NULL', 1: 'PROGBITS', 2: 'SYMTAB', 3: 'STRTAB', 4: 'RELA',
       5: 'HASH', 6: 'DYNAMIC', 7: 'NOTE', 8: 'NOBITS', 9: 'REL',
       11: 'DYNSYM', 14: 'INIT_ARRAY', 15: 'FINI_ARRAY'}
STT = {0: 'notype', 1: 'object', 2: 'func', 3: 'section', 4: 'file'}
STB = {0: 'local', 1: 'global', 2: 'weak'}


def fold(check, buf):
    """arm2hpdl's docheck(): add 16-bit little-endian words."""
    for i in range(0, len(buf) - 1, 2):
        check += (buf[i] << 0) | (buf[i + 1] << 8)
    if len(buf) & 1:
        check += buf[-1]
    return check


def _fold16(check):
    while check >> 16:
        check = (check & 0xffff) + (check >> 16)
    return check & 0xffff


def load(path):
    """Return (inner_elf_bytes, file_offset_of_inner_elf).

    The trailer (4 bytes) is excluded so the result is a complete ELF.
    """
    raw = open(path, 'rb').read()
    first = raw.find(b'\x7fELF')
    if first < 0:
        sys.exit("no ELF found")
    base = raw.find(b'\x7fELF', first + 1)
    if base < 0:
        base = first
    end = len(raw)
    # An arm2hpdl trailer (ff ff xx xx) closes the blob.
    if raw[-4:-2] == b'\xff\xff':
        end = len(raw) - 4
    # Sanity: an intact section table tells us the exact ELF size.
    shoff, = struct.unpack('>I', raw[base + 0x20:base + 0x24])
    shnum, = struct.unpack('>H', raw[base + 0x30:base + 0x32])
    shent, = struct.unpack('>H', raw[base + 0x2e:base + 0x30])
    if shoff and shnum and shent:
        want = base + shoff + shnum * shent
        if want <= end:
            end = want
    return raw[base:end], base


class Image:
    def __init__(self, path):
        self.path = path
        self.raw = open(path, 'rb').read()
        self.elf, self.base_off = load(path)

        (self.e_type, self.e_machine, _, self.e_entry, self.e_phoff,
         self.e_shoff, _, _, self.e_phentsize, self.e_phnum,
         _, self.e_shnum, self.e_shstrndx) = \
            struct.unpack('>HHIIIIIHHHHHH', self.elf[16:52])

        # ---- program headers (PT_LOAD) ----
        self.segs = []
        for i in range(self.e_phnum):
            o = self.e_phoff + i * self.e_phentsize
            if o + 32 > len(self.elf):
                break
            t, off, va, pa, fs, ms, fl, al = struct.unpack(
                '>IIIIIIII', self.elf[o:o + 32])
            if t != 1:
                continue
            self.segs.append(dict(idx=i, off=off, va=va, fs=fs, ms=ms, fl=fl))

        # ---- section headers ----
        self.secs = []
        for i in range(self.e_shnum):
            o = self.e_shoff + i * 40
            if o + 40 > len(self.elf):
                break
            (name, typ, flags, addr, off, size, link, info,
             align, entsz) = struct.unpack('>IIIIIIIIII', self.elf[o:o + 40])
            self.secs.append(dict(idx=i, nameoff=name, type=typ, flags=flags,
                                  addr=addr, off=off, size=size, link=link,
                                  info=info, entsz=entsz))
        shstr = self.secs[self.e_shstrndx] if self.e_shstrndx < len(self.secs) else None
        self._shstr = (self.elf[shstr['off']:shstr['off'] + shstr['size']]
                       if shstr else b'')

        # ---- symbol table (.symtab) ----
        self.syms = []
        sym = next((s for s in self.secs if s['type'] == 2), None)
        if sym:
            strsec = self.secs[sym['link']] if sym['link'] < len(self.secs) else None
            strtab = (self.elf[strsec['off']:strsec['off'] + strsec['size']]
                      if strsec else b'')
            n = sym['size'] // 16
            for i in range(n):
                o = sym['off'] + i * 16
                nm, val, sz, x = struct.unpack('>IIII', self.elf[o:o + 16])
                name = self._cstr(strtab, nm)
                info, other, shndx = (x >> 24) & 0xff, (x >> 16) & 0xff, x & 0xffff
                self.syms.append(dict(name=name, addr=val, size=sz,
                                      bind=STB.get(info >> 4, info >> 4),
                                      type=STT.get(info & 0xf, info & 0xf),
                                      shndx=shndx))
        self._byname = {}
        for s in self.syms:
            self._byname.setdefault(s['name'], []).append(s)

        self.md = Cs(CS_ARCH_ARM, CS_MODE_ARM | CS_MODE_BIG_ENDIAN)
        self.md.detail = True

    # ------------------------------------------------------------------
    @staticmethod
    def _cstr(buf, off):
        if off >= len(buf):
            return None
        end = buf.find(b'\x00', off)
        if end < 0:
            return None
        b = buf[off:end]
        return b.decode() if all(32 <= c < 127 for c in b) else None

    def secname(self, idx):
        if idx >= len(self.secs):
            return {0xfff1: 'ABS', 0xfff2: 'COMMON', 0: 'UND'}.get(idx, '?%d' % idx)
        return self._cstr(self._shstr, self.secs[idx]['nameoff']) or '?'

    def section(self, name):
        for s in self.secs:
            if self.secname(s['idx']) == name:
                return s
        return None

    def va2off(self, va):
        """Virtual address -> file offset, or None if not file-backed."""
        for s in self.segs:
            if s['fs'] and s['va'] <= va < s['va'] + s['fs']:
                return s['off'] + (va - s['va'])
        return None

    def off2va(self, off):
        for s in self.segs:
            if s['fs'] and s['off'] <= off < s['off'] + s['fs']:
                return s['va'] + (off - s['off'])
        return None

    def code(self, va, size):
        o = self.va2off(va)
        if o is None:
            return None
        return self.elf[o:o + size]

    # ---- symbol queries ------------------------------------------------
    def symbols(self, kind=None, executable_only=False):
        """Symbols as {name, addr, size, type, shndx}, sorted by address.

        kind: None | 'func' | 'object'
        """
        out = []
        for s in self.syms:
            if kind and s['type'] != kind:
                continue
            if executable_only and not (self.secs[s['shndx']]['flags'] & 0x4
                                        if s['shndx'] < len(self.secs) else False):
                continue
            if s['shndx'] in (0, 0xfff1):
                continue          # UND / ABS placeholders
            out.append(s)
        out.sort(key=lambda d: d['addr'])
        return out

    def name_at(self, va):
        """Best-effort symbol name for a virtual address."""
        best = None
        for s in self.syms:
            if s['addr'] == va and s['name']:
                return s['name']
            if s['addr'] <= va < s['addr'] + max(s['size'], 1) and s['name']:
                if best is None or s['addr'] > best['addr']:
                    best = s
        return best['name'] if best else None

    def file_map(self):
        """FILE symbol -> list of symbols defined after it (source attribution)."""
        out, cur = {}, None
        for s in self.syms:
            if s['type'] == 'file':
                cur = s['name']
                out.setdefault(cur, [])
            elif cur is not None:
                out[cur].append(s)
        return out

    def sweep(self, va, size):
        """Linear disassembly that survives embedded data.

        capstone halts at the first undecodable word (jump tables, literal
        pools and string constants all live in .text here).  Track where it
        actually stopped, step over the bad word, and resume.
        """
        blob = self.code(va, size)
        if not blob:
            return
        i, end = 0, len(blob)
        while i + 4 <= end:
            last = None
            for ins in self.md.disasm(blob[i:], va + i):
                last = ins
                yield ins
            if last is None:
                i += 4                      # bad word: skip and resume
            else:
                i = (last.address - va) + last.size

    # ---- literal-pool / MMIO dereference ------------------------------
    def word(self, va):
        b = self.code(va, 4)
        return struct.unpack('>I', b)[0] if b and len(b) == 4 else None

    def string_at(self, va, maxlen=96):
        o = self.va2off(va)
        if o is None:
            return None
        e = self.elf.find(b'\x00', o)
        if e < 0 or not (1 <= e - o <= maxlen):
            return None
        b = self.elf[o:e]
        return b.decode() if all(32 <= c < 127 for c in b) else None

    def literal(self, ins):
        """For an ldr/str [pc, #imm], resolve the pooled word."""
        if not ins.mnemonic.startswith(('ldr', 'str')) or '[pc' not in ins.op_str:
            return None, None
        if '#' not in ins.op_str:
            return None, None
        try:
            imm = int(ins.op_str.rsplit('#', 1)[1].split(']')[0], 0)
        except ValueError:
            return None, None
        va = ((ins.address + 8) & ~3) + imm
        return va, self.word(va)


def symbols_from(path, kind=None):
    """Convenience wrapper: recover the symbol table for an image."""
    return Image(path).symbols(kind)


# ----------------------------------------------------------------------
def main(path):
    img = Image(path)

    print("=== container ===")
    print("  inner ELF: %d bytes   (envelope offset %d)"
          % (len(img.elf), img.base_off))
    print("  e_entry   : 0x%08x   (outer stub's entry supplies 0x01803000)"
          % img.e_entry)

    print("\n=== sections (%d) ===" % len(img.secs))
    print("  %-3s %-14s %-10s %-10s %-9s %s"
          % ("#", "name", "type", "addr", "off", "size"))
    for s in img.secs:
        print("  %-3d %-14s %-10s 0x%08x 0x%08x 0x%-7x"
              % (s['idx'], img.secname(s['idx']), SHT.get(s['type'], '?'),
                 s['addr'], s['off'], s['size']))

    print("\n=== memory map (PT_LOAD) ===")
    print("  %-3s %-10s %-10s %-9s %-9s %s"
          % ("#", "va", "file off", "filesz", "memsz", "perms"))
    for s in img.segs:
        p = ('r' if s['fl'] & 4 else '-') + \
            ('w' if s['fl'] & 2 else '-') + \
            ('x' if s['fl'] & 1 else '-')
        print("  %-3d 0x%08x 0x%08x 0x%-8x 0x%-8x %s"
              % (s['idx'], s['va'], s['off'], s['fs'], s['ms'], p))

    text = img.section('.text')
    print("\n=== .text ===")
    if text:
        print("  va 0x%08x  size 0x%x (%d bytes)"
              % (text['addr'], text['size'], text['size']))

    funcs = img.symbols('func')
    objs = img.symbols('object')
    tx = [s for s in img.syms if s['shndx'] == 2]
    print("\n=== symbols ===")
    print("  FUNC   %d   OBJECT %d   FILE %d   total %d"
          % (len(funcs), len(objs),
             sum(1 for s in img.syms if s['type'] == 'file'), len(img.syms)))

    print("\n  --- .txtable (37 ThreadX API trampolines; platform-provided) ---")
    for s in sorted(tx, key=lambda x: x['addr']):
        if s['type'] == 'func':
            print("     0x%03x  %s" % (s['addr'], s['name']))
    print("     (%d entries)" % sum(1 for s in tx if s['type'] == 'func'))

    print("\n  --- first functions ---")
    print("  %-34s %-10s %s" % ("name", "addr", "size"))
    for s in funcs[:20]:
        print("  %-34s 0x%08x 0x%-6x" % (s['name'], s['addr'], s['size']))
    print("  ... (%d more)" % max(0, len(funcs) - 20))

    print("\n  --- largest OBJECTs (globals / buffers) ---")
    for s in sorted(objs, key=lambda x: -x['size'])[:20]:
        print("  0x%08x sz=0x%-7x %-30s %s"
              % (s['addr'], s['size'], s['name'], img.secname(s['shndx'])))

    print("\n  --- source files (%d) ---" % sum(1 for s in img.syms
                                               if s['type'] == 'file'))
    for s in img.syms:
        if s['type'] == 'file' and s['name']:
            print("     %s" % s['name'])

    # write a consolidated JSON for the other tools
    import json
    dump = {'sections': [dict(s, name=img.secname(s['idx'])) for s in img.secs],
            'segments': img.segs,
            'symbols': img.symbols()}
    with open('symbols.json', 'w') as fh:
        json.dump(dump, fh, indent=1)
    print("\n  wrote symbols.json (%d symbols, %d sections)"
          % (len(dump['symbols']), len(dump['sections'])))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1
         else '/usr/share/foo2zjs/firmware/sihp1000.dl')
