# open-firmware — reverse-engineering notes for a clean-room LJ1000 firmware

Goal: replace the HP LaserJet 1000's downloaded firmware with an open-source
implementation written in C against published specifications (JBIG
ISO/IEC 11544, PJL/IEEE 1284, and the GPL `foo2zjs` encoder), not against a
transliteration of HP's binary.

**This directory contains no HP firmware.** Everything here is analysis
code and notes; the image itself is fetched at install time by
`../fetch-firmware.sh` and is excluded from git, exactly like the rest of
this repo.

---

## Where we are

Milestone reached: **an image we modified ourselves boots on real hardware
and prints.**

The first experiment patched four payload bytes in the shipped image
(`FWVER:20011207` → `20991231`) plus the two bytes of the container
checksum that cover them, uploaded it, and read the change back over USB:

```
device id (after): MFG:...;DES:hp LaserJet 1000;FWVER:20991231;
```

The job printed with that marker on it. This proves the whole path —
container construction, checksum, download state, execution, and the
device-ID string as a debug channel — before any of our own code exists.

That image differed from stock in exactly 6 of 114,233 bytes; blob 1 was
byte-identical.

### Things that were wrong or unknown before

* The inner ELF is **not stripped**: section table (27 entries), `.symtab`
  (705 symbols) and `.strtab` are all intact. No heuristic recovery needed.
* `USBDEVFS_RESET` does **not** reboot the MCU. An upload issued while
  firmware is already running is silently swallowed by the live application.
  Only a genuine power cycle returns the printer to download mode.
* The ThreadX kernel is **not in the image at all** — see below.

---

## Container format

Verified against `../tools/arm2hpdl.c` and reproduced byte-for-byte
(`dlpatch.py --verify` re-signs the untouched stock image to its known-good
sha256 `6ef9ee2c…677e`).

```
[0..17)        filhdr  be ef "ABCDEFGH" size = len + 16, then 00 00 00
[17..29)       sechdr  c0 de "ABCDEFGH" 00 00
[29..173)      blob 1: outer stub ELF (144 bytes)
               e_entry = 0x01803000, does *(u32*)0x01030064 = 0x33; bx lr
[173..177)     trailer  ff ff ~check          check = 0x7498
[177..189)     sechdr  c0 de "ABCDEFGH" 00 00
[189..114229)  blob 2: application ELF (114040 bytes = 0x1bd78)
[114229..114233) trailer ff ff ~check          check = 0x14d8
```

`check` = sum of little-endian 16-bit words over `sechdr + payload`, folded
to 16 bits, then complemented (`docheck()` in `arm2hpdl.c`).

The application ELF ends exactly at its section-header table (`e_shoff` +
`27 * 40` = `0x1bd78`), which is how its length is determined unambiguously.

---

## Tooling

| tool | purpose |
|---|---|
| `tools/fw_map.py` | ELF/segment/section/symbol mapper; `sweep()` disassembler; runs standalone for a full report |
| `tools/dlpatch.py` | patch bytes by VA or ELF offset and re-sign the container |
| `tools/mkelf.py` | extract the inner ELF + emit supplementary r2 flags |
| `tools/usb_regs.py` | literal-pool MMIO extractor |
| `../tools/arm2hpdl.c` | authoritative HP envelope/checksum (vendored from `foo2zjs`) |

Typical session:

```bash
python3 fw_map.py                      # full report, regenerates symbols.json
python3 dlpatch.py --verify             # prove the resign path against stock
python3 dlpatch.py --va 0x018030d0 --str 'FWVER:20991231;' --out /tmp/x.dl
sudo ../hp-lj1000-loadfw --force /tmp/x.dl
r2 -i lj1000.r2cmds -c 'aaa; pdf @sym.main; axt @sym.usbReadPIO' lj1000.elf
```

**r2 note:** do *not* pre-seed functions with `af+` before `aaa` — size-0
functions are never analysed. radare2 imports the ELF `.symtab` natively and
does the right thing on its own (425 functions, correct names).

---

## Memory map

```
0x00000000   .reset     4 B      platform-owned (file contains `b .`)
0x00000100   .txtable   0x94     37 ThreadX trampolines, platform-owned
0x01000fc0   MMIO       (single reference)
0x01010000   USB controller            0x01010090, 0x010100c0
0x01020000   image / video             0x01024000
0x01025000   JBIG                       0x0102a000
0x017df000   engine block A   (offsets 0x004..0x010)
0x017e0000   engine block B   (offsets 0x004..0x034)
0x017f0000   engine block C   (offsets 0x000..0x018)
0x01030000   interrupt controller
0x01040000   misc                      0x01044040
0x01800000   ROM: ppfnIrqHandler (interrupt vector table)
0x01800684   ROM: ptRomWatchdog
0x01803000   .startup     0x68       entry point
0x01803068   .verdata     0x78       "$$DEVICE_ID_STRING$$" + device ID
0x018030e0   .text        0x151a0
0x01818358   .rodata      0xf74      contains the MMIO pointer table
0x018192cc   .secinfo     0x20
0x018192f0   .data        0x2ac
0x0181959c   .bss         0x81bc
0x01821760   .heap        64 K
0x01831760   .stack       4 K
0x01832760   .jobmem      0xcd8a0    ~841 K raster buffer
```

The MMIO register addresses are held in one contiguous pointer table in
`.rodata`, `0x0181836c`–`0x01818540`. Code reaches them indirectly
(`ldr r2, =0x018184xx; ldr r1, [r2]`), so a naive literal-pool scan misses
most of them.

---

## Startup path

Entry `0x01803000` (blob 1's `e_entry`, and `.startup` in blob 2):

1. save r4–lr to `0x018192f0`, compute load delta, call crt0
   (`__ghs_ind_crt0` at `0x01817678`, 784 B) with a link table
   `{0x018030e0, 0, 0x018192f0}` → **all load deltas are 0** (linked at load
   addresses, zero relocations applied).
2. crt0: `memset` over `(dest,val,len)` triples → clears BSS
   (`0x0181959c`, `0x81bc`); then calls `main(1, …)`.
3. `main` at `0x018030e0` (144 B) creates the `startup` ThreadX thread:

   ```c
   tx_thread_create(&tSS, "startup", StartupShutdown, tx_thread_identify(),
                    ssStack, 0x2000, 15, 15, 1 /*timeslice*/, 1 /*auto-start*/);
   ```

4. `StartupShutdown` at `0x018031f4` is the real application main:

   ```
   zjStatusInit, channelMgrInit, zjsParserInit, pmInit, paperPathInit,
   imInit(1), engInit, zjPjlInit, tobagoStatusStartup,
   tcmdParserInit, usbDrvInit, _tx_thread_suspend_api(self),
   pmJobIdleWait(-1),
   zjPjlShutdown, engShutdown, imShutdown, paperPathShutdown, pmShutdown,
   zjStatusShutdown, jmShutdown, zjmDelete, zmsgShutdown,
   zjThreadShutdown, intShutdown, …watchdog cleanup…
   ```

---

## The ThreadX problem

`.txtable` holds 37 four-byte entries at `0x100`–`0x194`
(`_tx_block_allocate` at `0x100` … `_tx_thread_create` at `0x138`,
`_tx_thread_identify` at `0x140`, `_tx_thread_interrupt_control` at `0x180`).

* All 37 `_tx_*` symbols are in `.txtable`; **zero** implementations exist in
  `.text` — the ThreadX kernel is not part of this image.
* **134 call sites** across the application branch into 30 of the 37 slots
  (`_tx_thread_interrupt_control` alone has 26).
* The shipped bytes there are `0xeafffffe` = `b .`, which would hang
  instantly — and `main`'s first action is `bl 0x140`.

Since the printer works, the platform must supply that region (ROM or a
loader that writes it). crt0 does not populate it: no `SHT_REL`/`SHT_RELA`
sections, `.fixaddr`/`.fixtype` are empty, and the relocation stream's start
equals its end.

**Consequence for us:** our own image must either populate those slots, avoid
calling through them entirely, or rely on the platform table. The bring-up
image deliberately avoids ThreadX.

---

## Getting an image to run

`GET_DEVICE_ID` is answered by the **ROM** before firmware is loaded:

```
no firmware:      …DES:hp LaserJet 1000;\0\0          (no FWVER)
firmware running: …DES:hp LaserJet 1000;FWVER:…;
```

so the `FWVER` field is the marker that our image is the thing running. It is
reached through `usbControlLoop` → literal `0x01808c3c` → `0x0180307c`, and
there is exactly one copy of the string in the image.

Procedure for any experiment:

```bash
sudo systemctl mask hp-lj1000-loadfw.service   # optional; udev rule is enough
sudo mv /etc/udev/rules.d/99-hp-lj1000-firmware.rules{,.disabled}
sudo udevadm control --reload-rules
# power-cycle the printer   <-- physical; uhubctl finds no compatible hub
sudo ./hp-lj1000-loadfw --force your-image.dl
# restore the rule afterwards
```

A power cycle is mandatory. `USBDEVFS_RESET` only resets the USB controller;
the MCU keeps running the old image and accepts the new bytes as ordinary
bulk data.

**This cannot brick the printer:** there is no writable engine flash. The
firmware lives only in RAM, and a power cycle with the stock udev rule in
place always returns to the known-good image.

---

## Next

1. Own linker script + `startup.S` + `mkdl.py`, boot an image whose startup
   is ours while the rest of the application is still HP's — proves the
   loader accepts a structure we produced.
2. Replace subsystems one at a time, keeping the device-ID string as the
   status channel throughout.
3. USB control loop first (it is the debug channel), then ZjStream receive
   (encoder already exists in `foo2zjs`), JBIG decode, PJL, engine layer.
