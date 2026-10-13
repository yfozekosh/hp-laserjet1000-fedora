# tools/

## arm2hpdl.c

Taken verbatim from the **foo2zjs** source tree
(<https://www.quirinux.org/printers/> mirror of the foo2zjs tarball, the
same source the upstream `getweb` script is part of).

foo2zjs is distributed under the **GNU General Public License** — see the
`LICENSE` section of the foo2zjs `README`.

Build it with:

```bash
cc -O2 -o arm2hpdl tools/arm2hpdl.c
```

### Do you actually need it?

Almost certainly not. The `sihp1000.img` fetched by `../fetch-firmware.sh`
already carries HP's download header, so `arm2hpdl` simply copies it
through to stdout:

```bash
arm2hpdl sihp1000.img > sihp1000.dl     # identical output, same size
```

You need it only if you start from a **raw ARM ELF**, e.g. the
`sihp1000.img` extracted from HP's own `lj1488en.exe` installer. `arm2hpdl`
adds the `0xBE EF "ABCDEFGH"` header (or passes through an existing one).

```bash
arm2hpdl sihp1000.img > sihp1000.dl
sudo install -m 644 sihp1000.dl /usr/share/foo2zjs/firmware/sihp1000.dl
```
