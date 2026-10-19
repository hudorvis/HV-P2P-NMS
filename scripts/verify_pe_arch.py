"""Verify a native Windows x64 PE image without third-party packages."""
from __future__ import annotations
import struct
import sys
from pathlib import Path


def machine_type(path: Path) -> int:
    with path.open("rb") as fp:
        if fp.read(2) != b"MZ":
            raise ValueError("Not a Windows PE executable (MZ header missing)")
        fp.seek(0x3C)
        offset = struct.unpack("<I", fp.read(4))[0]
        if offset < 64 or offset > 16 * 1024 * 1024:
            raise ValueError("Invalid PE header offset")
        fp.seek(offset)
        if fp.read(4) != b"PE\x00\x00":
            raise ValueError("Invalid PE signature")
        return struct.unpack("<H", fp.read(2))[0]


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: verify_pe_arch.py executable.exe")
    machine = machine_type(Path(sys.argv[1]))
    print(f"Windows PE machine type: 0x{machine:04x}")
    if machine != 0x8664:
        raise SystemExit("Wrong architecture: expected native Windows x64 (AMD64 / 0x8664)")
    print("Windows x64 architecture check PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
