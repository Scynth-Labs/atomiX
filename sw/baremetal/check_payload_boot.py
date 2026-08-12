#!/usr/bin/env python3
"""Run a bare-metal payload the way a board actually receives one.

Every other bare-metal check hands the simulator a RAM image, which is the
same shortcut the Gowin bring-up flow takes when it bakes `RAM_INIT_FILE` into
synthesis.  That shortcut is why adding a program used to be a hardware event:
a baked payload is part of the bitstream, so every example had its own
placement, its own timing, and its own hash, and `role.tpu-lite` once stopped
fitting because of a software change.

So this boots the machine from the immutable ROM into blank RAM and *uploads*
the program over the UART, as `AXK1 | length | crc32 | payload` — the same
frame `sw/host/axhost.py --upload-kernel` sends to the real board, into the
same loader.  What it proves is that a program is a payload and not a
hardware revision: the uploaded image must reach exactly the state the baked
image reaches, byte for byte.

Anything left in the input stream after the frame stays queued for the program
itself, so a game's key tape simply follows its binary.

    python3 check_payload_boot.py --payload build/ram32768/snake_fast.bin \\
        --keys tests-snake-keys.txt --expect 'snake: QUIT' --expect 'drops=0 '
"""

import argparse
import pathlib
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
BLANK_RAM = ROOT / "sw/bootrom/blank.hex"
CONFIG = ROOT / "configs/sim-bram.json"


def frame(payload: bytes) -> bytes:
    return b"AXK1" + struct.pack("<II", len(payload),
                                 zlib.crc32(payload) & 0xFFFFFFFF) + payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True,
                        help="raw .bin linked at 0x80000000")
    parser.add_argument("--keys", help="bytes to leave queued for the program")
    parser.add_argument("--expect", action="append", default=[],
                        help="text the transcript must contain (repeatable)")
    parser.add_argument("--ram-bytes", type=int, default=32768)
    parser.add_argument("--max-cycles", type=int, default=12000000)
    parser.add_argument("--label", default="payload")
    # The boards set SYNC_READ=1 so RAM and the boot ROM map to BSRAM instead of
    # LUTs, which costs one wait state per access.  Default to that timing: this
    # gate exists to stand behind a physical claim, so it should run the read
    # timing the board runs, not the simulator-friendly combinational one.
    parser.add_argument("--sync-read", type=int, default=1, choices=(0, 1),
                        help="ROM/RAM read timing (1 = registered, as on board)")
    args = parser.parse_args()

    payload = pathlib.Path(args.payload).read_bytes()
    boot_rom = ROOT / f"sw/bootrom/build/uart-ram{args.ram_bytes}/bootrom.hex"
    if not boot_rom.exists():
        return fail(f"loader not built: {boot_rom}\n"
                    f"  make -C sw/bootrom images MODE=uart "
                    f"RAM_BYTES={args.ram_bytes} "
                    f"BUILD_DIR=build/uart-ram{args.ram_bytes}")

    # The loader keeps the top of RAM for its own stack and refuses anything
    # that would reach it. Checking here turns "the upload was rejected" into
    # the specific sentence a payload author needs.
    limit = args.ram_bytes - 4096
    if len(payload) > limit:
        return fail(f"{args.label}: payload is {len(payload)} bytes, "
                    f"over the loader's {limit}-byte limit for "
                    f"{args.ram_bytes // 1024} KiB of RAM")

    stream = frame(payload)
    if args.keys:
        stream += pathlib.Path(args.keys).read_bytes()

    with tempfile.NamedTemporaryFile(suffix=".uart") as uart:
        uart.write(stream)
        uart.flush()
        result = subprocess.run([
            "make", "-s", "--no-print-directory", "-C", str(ROOT / "sim/soc"),
            "run", f"COMPONENT_CONFIG={CONFIG}",
            f"RAM_INIT_FILE={BLANK_RAM}", f"ROM_INIT_FILE={boot_rom}",
            f"RAM_BYTES={args.ram_bytes}", "RESET_PC=0x00001000",
            f"SYNC_READ={args.sync_read}",
            f"UART_INPUT_FILE={uart.name}", f"MAX_CYCLES={args.max_cycles}",
            f"BUILD_ID=payload-boot-sync{args.sync_read}",
        ], cwd=ROOT, capture_output=True, timeout=1800)

    transcript = result.stdout
    if result.returncode != 0:
        sys.stderr.write(result.stderr.decode("latin-1", "replace"))
        return fail(f"{args.label}: simulation exit {result.returncode}")

    ack = b"AXOK" + struct.pack("<I", len(payload))
    if ack not in transcript:
        return fail(f"{args.label}: loader did not accept the upload "
                    f"(no AXOK for {len(payload)} bytes)")
    timing = "registered (board)" if args.sync_read else "combinational"
    print(f"{args.label}: PASS (loader accepted {len(payload)} bytes into "
          f"blank RAM and started them; {timing} ROM/RAM reads)")

    for wanted in args.expect:
        if wanted.encode() not in transcript:
            return fail(f"{args.label}: uploaded program did not reach the "
                        f"same state as the baked image: missing {wanted!r}")
    if args.expect:
        print(f"{args.label}: PASS (uploaded image reaches the baked image's "
              f"exact final state)")
    return 0


def fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
