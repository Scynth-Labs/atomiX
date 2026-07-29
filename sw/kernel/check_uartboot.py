#!/usr/bin/env python3
"""Verify the immutable UART ROM with errors and a full uploaded aXos."""
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / "sw/kernel/build/axos_boot.bin"
BOOT_ROM = ROOT / "sw/bootrom/build/uart-ram131072/bootrom.hex"
BLANK_RAM = ROOT / "sw/bootrom/blank.hex"
CONFIG = ROOT / "configs/sim-bram.json"


def frame(payload: bytes, crc=None) -> bytes:
    checksum = zlib.crc32(payload) & 0xFFFFFFFF if crc is None else crc
    return b"AXK1" + struct.pack("<II", len(payload), checksum) + payload


def run(data: bytes, max_cycles: int) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".uart") as stream:
        stream.write(data)
        stream.flush()
        result = subprocess.run([
            "make", "-s", "--no-print-directory", "-C", str(ROOT / "sim/soc"),
            "run", f"COMPONENT_CONFIG={CONFIG}",
            f"RAM_INIT_FILE={BLANK_RAM}", f"ROM_INIT_FILE={BOOT_ROM}",
            "RESET_PC=0x00001000", f"UART_INPUT_FILE={stream.name}",
            f"MAX_CYCLES={max_cycles}", "BUILD_ID=uartboot-full",
        ], cwd=ROOT, capture_output=True, timeout=240)
    # Error-path fixtures deliberately leave the ROM waiting for another frame,
    # so the harness reaches MAX_CYCLES and returns 2. A successful kernel boot
    # exits through the normal finisher and must return 0.
    if result.returncode not in (0, 2):
        raise SystemExit(
            f"[uartboot] simulation exit {result.returncode}\n"
            + result.stderr.decode("latin-1", "replace"))
    return result.stdout


def main() -> None:
    corrupt = run(frame(b"bad kernel", crc=0), 20000)
    if b"AXER" + struct.pack("<I", 2) not in corrupt:
        raise SystemExit(f"[uartboot] corrupt CRC was not rejected: {corrupt!r}")
    print("[uartboot] corrupt CRC rejection: PASS")

    oversized = b"AXK1" + struct.pack("<II", 131072, 0)
    rejected = run(oversized, 10000)
    if b"AXER" + struct.pack("<I", 1) not in rejected:
        raise SystemExit(f"[uartboot] oversized image was not rejected: {rejected!r}")
    print("[uartboot] oversized image rejection: PASS")

    kernel = KERNEL.read_bytes()
    output = run(frame(kernel) + b"exit\n", 8000000)
    ack = b"AXOK" + struct.pack("<I", len(kernel))
    expected_tail = b"aXos: shell online\naXos> exit\n"
    if ack not in output or not output.endswith(expected_tail):
        raise SystemExit(
            f"[uartboot] full kernel boot mismatch\n"
            f"  ack: {ack!r}\n"
            f"  output: {output!r}")
    print(f"[uartboot] full aXos upload ({len(kernel)} bytes) + shell: PASS")


if __name__ == "__main__":
    main()
