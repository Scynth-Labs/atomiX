#!/usr/bin/env python3
"""axhost — host-side driver for the atomiX shell + role platform.

Speaks the aX host-link protocol (docs/host-protocol.md) to the aXos host-link
service running on the FPGA shell.  The virtual-pipe backend talks to Verilator;
the serial backend uses the same frames on a physical board.

    axhost.py --demo --image <axos.hex> --config <profile.json>
    axhost.py --fast-switch --upload-kernel <axos.bin> \
        --serial /dev/ttyUSB1 --baud 921600

`--demo` runs PING, INFO, and a ROLE_RUN job against role.loopback and checks
every response, which is what `make -C sw/kernel check-hostlink` invokes.
`--upload-kernel` installs aXos through the immutable ROM before the host-link
session. `--fast-switch` then loads and runs two GPU programs without restarting
aXos or rebuilding/reloading the FPGA.
"""
import argparse
import os
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
import tty
import zlib
from pathlib import Path

from gpu_programs import (
    GPU_ADD, GPU_ADDI, GPU_HALT, GPU_LDX, GPU_MUL, GPU_MULI, GPU_STX,
    GPU_TID, gpu_insn, reviewed_fast_switch_programs,
)

ROOT = Path(__file__).resolve().parents[2]

REQ_SYNC = 0xA5
RSP_SYNC = 0x5A
OP_PING = 0x01
OP_INFO = 0x02
OP_ROLE_RUN = 0x10
OP_TPU_GEMM = 0x11
OP_GPU_RUN = 0x12
OP_GPU_LOAD = 0x13
OP_GPU_EXEC = 0x14
OP_BYE = 0x7F
ST_OK = 0x00
ROLE_ID_LOOPBACK = 0x4C4F4F50  # "LOOP"
ROLE_ID_TPU = 0x5450554C       # "TPUL"
ROLE_ID_GPU = 0x47505543       # "GPUC"

def request(op, payload=b""):
    return bytes([REQ_SYNC, op]) + struct.pack("<H", len(payload)) + payload


def role_run_payload(words):
    return struct.pack("<H", len(words)) + b"".join(
        struct.pack("<I", w & 0xFFFFFFFF) for w in words)


def parse_responses(data):
    """Return a list of (status, payload) frames, skipping any non-frame
    preamble and resynchronizing on the response sync byte."""
    frames = []
    i = 0
    while i + 4 <= len(data):
        if data[i] != RSP_SYNC:
            i += 1
            continue
        status = data[i + 1]
        length = data[i + 2] | (data[i + 3] << 8)
        if i + 4 + length > len(data):
            break
        frames.append((status, data[i + 4:i + 4 + length]))
        i += 4 + length
    return frames


class SimPipe:
    """Virtual-pipe transport: drive a Verilator simulation of the shell."""

    def __init__(self, image, config, max_cycles, boot_rom=None,
                 kernel_binary=None):
        self.image = image
        self.config = config
        self.max_cycles = max_cycles
        self.boot_rom = boot_rom
        self.kernel_binary = kernel_binary

    def exchange(self, request_bytes, expected_frames=None,
                 expected_marker=None):
        del expected_frames
        del expected_marker
        booting = self.boot_rom is not None
        if booting:
            request_bytes = kernel_upload_frame(
                Path(self.kernel_binary).read_bytes()) + request_bytes
        with tempfile.NamedTemporaryFile(suffix=".req", delete=False) as handle:
            handle.write(request_bytes)
            req_path = handle.name
        command = [
            "make", "-s", "--no-print-directory", "-C", str(ROOT / "sim/soc"),
            "run",
            f"RAM_INIT_FILE={ROOT / 'sw/bootrom/blank.hex' if booting else self.image}",
            f"RESET_PC={'0x00001000' if booting else '0x80000000'}",
            f"COMPONENT_CONFIG={self.config}", f"MAX_CYCLES={self.max_cycles}",
            f"UART_INPUT_FILE={req_path}", "BUILD_ID=hostlink",
        ]
        if booting:
            command.append(f"ROM_INIT_FILE={self.boot_rom}")
        result = subprocess.run(command, cwd=ROOT, capture_output=True,
                                timeout=240)
        if result.returncode:
            sys.stderr.write(result.stderr.decode("latin-1", "replace"))
            raise SystemExit(f"axhost: simulation exit {result.returncode}")
        if booting and b"AXOK" not in result.stdout:
            raise SystemExit("axhost: UART bootloader did not acknowledge kernel")
        return result.stdout


class SerialPipe:
    """POSIX serial transport; works directly with WSL-attached ttyUSB ports."""

    def __init__(self, device, baud, timeout):
        self.device = device
        self.baud = baud
        self.timeout = timeout

    def exchange(self, request_bytes, expected_frames=None,
                 expected_marker=None):
        baud_const = getattr(termios, f"B{self.baud}", None)
        if baud_const is None:
            raise SystemExit(f"axhost: unsupported serial baud {self.baud}")
        fd = os.open(self.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            tty.setraw(fd)
            attrs = termios.tcgetattr(fd)
            attrs[4] = baud_const
            attrs[5] = baud_const
            attrs[2] |= termios.CLOCAL | termios.CREAD
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            termios.tcflush(fd, termios.TCIOFLUSH)

            sent = 0
            while sent < len(request_bytes):
                _, writable, _ = select.select([], [fd], [], self.timeout)
                if not writable:
                    raise SystemExit("axhost: serial write timeout")
                sent += os.write(fd, request_bytes[sent:])
            termios.tcdrain(fd)

            data = bytearray()
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                readable, _, _ = select.select([fd], [], [], 0.05)
                if readable:
                    chunk = os.read(fd, 4096)
                    if chunk:
                        data.extend(chunk)
                        if expected_marker:
                            marker = data.find(expected_marker)
                            if marker >= 0 and len(data) >= marker + 8:
                                break
                        if (expected_frames is not None and
                                len(parse_responses(data)) >= expected_frames):
                            break
            return bytes(data)
        finally:
            os.close(fd)


def kernel_upload_frame(binary):
    crc = zlib.crc32(binary) & 0xFFFFFFFF
    return b"AXK1" + struct.pack("<II", len(binary), crc) + binary


def upload_kernel(pipe, path):
    binary = Path(path).read_bytes()
    raw = pipe.exchange(kernel_upload_frame(binary),
                        expected_marker=b"AXOK")
    marker = raw.find(b"AXOK")
    if marker < 0 or marker + 8 > len(raw):
        raise SystemExit(f"axhost: kernel upload not acknowledged: {raw!r}")
    accepted = struct.unpack("<I", raw[marker + 4:marker + 8])[0]
    if accepted != len(binary):
        raise SystemExit(
            f"axhost: loader accepted {accepted} bytes, expected {len(binary)}")
    print(f"KERNEL -> uploaded {accepted} bytes, CRC32 verified, aXos started")


# Each builder returns (op, request_payload, expected_role_id, verify).  `verify`
# takes the job response payload, raises on mismatch, and returns a summary
# line.  The reference result is computed here on the host, so the role RTL is
# checked against an independent model — not a hand-picked constant.
def build_loopback():
    words = [0x11111111, 0x22222222, 0x33333333, 0xDEADBEEF]

    def verify(resp):
        got = list(struct.unpack(f"<{len(words)}I", resp))
        if got != words:
            raise SystemExit(f"axhost: loopback result {got} != {words}")
        return "loopback copy " + " ".join(f"0x{w:08x}" for w in words)

    return OP_ROLE_RUN, role_run_payload(words), ROLE_ID_LOOPBACK, verify


def build_tpu():
    m = 8
    w = [[((r - c) % 7) - 3 for c in range(8)] for r in range(8)]
    a = [[((i + 2 * k) % 5) - 2 for k in range(8)] for i in range(m)]
    ref = [sum(a[i][k] * w[k][j] for k in range(8))
           for i in range(m) for j in range(8)]
    w_flat = [w[r][c] for r in range(8) for c in range(8)]
    a_flat = [a[i][k] for i in range(m) for k in range(8)]
    payload = bytes([m, 0]) + struct.pack("64b", *w_flat) + \
        struct.pack(f"{8 * m}b", *a_flat)

    def verify(resp):
        got = list(struct.unpack(f"<{m * 8}i", resp))
        if got != ref:
            raise SystemExit(f"axhost: tpu C {got} != reference {ref}")
        return f"int8 GEMM {m}x8x8 -> {m * 8} int32 results verified"

    return OP_TPU_GEMM, payload, ROLE_ID_TPU, verify


def build_gpu():
    n = 10
    base_a, base_b, base_c = 0, 64, 128
    ndata = base_c + n
    a = [i + 1 for i in range(n)]
    b = [100 + 2 * i for i in range(n)]
    data = [0] * ndata
    for i in range(n):
        data[base_a + i] = a[i]
        data[base_b + i] = b[i]
    # SIMT saxpy: C[tid] = 3*A[tid] + B[tid].
    prog = [
        gpu_insn(GPU_TID, rd=0),
        gpu_insn(GPU_LDX, rd=1, ra=0),
        gpu_insn(GPU_ADDI, rd=2, ra=0, imm=base_b),
        gpu_insn(GPU_LDX, rd=3, ra=2),
        gpu_insn(GPU_MULI, rd=1, ra=1, imm=3),
        gpu_insn(GPU_ADD, rd=1, ra=1, rb=3),
        gpu_insn(GPU_ADDI, rd=4, ra=0, imm=base_c),
        gpu_insn(GPU_STX, ra=4, rb=1),
        gpu_insn(GPU_HALT),
    ]
    ref = list(data)
    for i in range(n):
        ref[base_c + i] = (3 * a[i] + b[i]) & 0xFFFFFFFF
    payload = struct.pack("<HHH", n, len(prog), ndata) + \
        b"".join(struct.pack("<I", x) for x in prog) + \
        b"".join(struct.pack("<I", x & 0xFFFFFFFF) for x in data)

    def verify(resp):
        got = list(struct.unpack(f"<{ndata}I", resp))
        if got != ref:
            raise SystemExit(
                f"axhost: gpu C {got[base_c:]} != {ref[base_c:]}")
        return (f"SIMT saxpy over {n} threads -> "
                f"C[{base_c}:{base_c + n}] verified")

    return OP_GPU_RUN, payload, ROLE_ID_GPU, verify


BUILDERS = {"loopback": build_loopback, "tpu": build_tpu, "gpu": build_gpu}


def demo(pipe, role):
    op, job_payload, expect_id, verify = BUILDERS[role]()
    stream = request(OP_PING) + request(OP_INFO) + request(op, job_payload)
    expected = 3
    if isinstance(pipe, SimPipe):
        stream += request(OP_BYE)
    frames = parse_responses(pipe.exchange(stream, expected))

    # The BYE acknowledgement is optional (the model may halt as it drains), so
    # require the three meaningful responses in order.
    if len(frames) < 3:
        raise SystemExit(f"axhost: expected >=3 responses, got {len(frames)}")

    ping_status, ping_payload = frames[0]
    if ping_status != ST_OK or ping_payload != b"aXHL":
        raise SystemExit(f"axhost: bad PING response {frames[0]!r}")
    print(f"PING -> ok ({ping_payload.decode('ascii', 'replace')})")

    info_status, info_payload = frames[1]
    if info_status != ST_OK or len(info_payload) != 8:
        raise SystemExit(f"axhost: bad INFO response {frames[1]!r}")
    role_id, version = struct.unpack("<II", info_payload)
    if role_id != expect_id:
        raise SystemExit(
            f"axhost: INFO role_id 0x{role_id:08x} != {role} 0x{expect_id:08x}")
    print(f"INFO -> role_id=0x{role_id:08x} ({role}) version={version}")

    job_status, job_resp = frames[2]
    if job_status != ST_OK:
        raise SystemExit(f"axhost: job status {job_status}, frame {frames[2]!r}")
    print(f"JOB  -> {verify(job_resp)}")
    print(f"axhost: host-link demo PASS "
          f"(host drove role.{role} over the link)")


def gpu_load_payload(program):
    return struct.pack("<H", len(program)) + b"".join(
        struct.pack("<I", word) for word in program)


def gpu_exec_payload(nthreads, data):
    return struct.pack("<HH", nthreads, len(data)) + b"".join(
        struct.pack("<I", word & 0xFFFFFFFF) for word in data)


def fast_switch(pipe, baud):
    """Switch between two GPU programs in one live shell session."""
    programs = reviewed_fast_switch_programs()

    requests = [request(OP_PING), request(OP_INFO)]
    for program in programs:
        requests.append(request(OP_GPU_LOAD, gpu_load_payload(program["words"])))
        requests.append(request(
            OP_GPU_EXEC,
            gpu_exec_payload(program["nthreads"], program["data"]),
        ))
    stream = b"".join(requests)
    if isinstance(pipe, SimPipe):
        stream += request(OP_BYE)
    wall_start = time.monotonic()
    frames = parse_responses(pipe.exchange(stream, 6))
    wall_ms = (time.monotonic() - wall_start) * 1000.0
    if len(frames) < 6:
        raise SystemExit(f"axhost: expected >=6 responses, got {len(frames)}")
    if frames[0] != (ST_OK, b"aXHL"):
        raise SystemExit(f"axhost: bad PING response {frames[0]!r}")
    if frames[1][0] != ST_OK or len(frames[1][1]) != 8:
        raise SystemExit(f"axhost: bad INFO response {frames[1]!r}")
    role_id, version = struct.unpack("<II", frames[1][1])
    if role_id != ROLE_ID_GPU:
        raise SystemExit(f"axhost: expected GPU role, got 0x{role_id:08x}")

    results = []
    for index, program in enumerate(programs):
        name = program["name"]
        load_frame = frames[2 + index * 2]
        exec_frame = frames[3 + index * 2]
        expected = program["expected"]
        if load_frame[0] != ST_OK or len(load_frame[1]) != 4:
            raise SystemExit(f"axhost: {name} LOAD failed: {load_frame!r}")
        load_cycles = struct.unpack("<I", load_frame[1])[0]
        if exec_frame[0] != ST_OK or len(exec_frame[1]) != 4 + 4 * len(expected):
            raise SystemExit(f"axhost: {name} EXEC failed: {exec_frame!r}")
        exec_cycles = struct.unpack("<I", exec_frame[1][:4])[0]
        got = list(struct.unpack(
            f"<{len(expected)}I", exec_frame[1][4:]))
        expected_u32 = [word & 0xFFFFFFFF for word in expected]
        if got != expected_u32:
            raise SystemExit(f"axhost: {name} result mismatch")
        results.append((name, load_cycles, exec_cycles, len(load_frame[1])))

    kernel_bytes = len(gpu_load_payload(programs[0]["words"]))
    wire_ms = (4 + kernel_bytes) * 10_000.0 / baud
    print(f"PING -> ok; GPUC version={version}")
    for name, load_cycles, exec_cycles, _ in results:
        print(f"{name:10s} LOAD={load_cycles} FPGA cycles, "
              f"EXEC={exec_cycles} FPGA cycles, result verified")
    print(f"kernel switch payload: {kernel_bytes} bytes "
          f"(~{wire_ms:.2f} ms at {baud} baud including frame)")
    if isinstance(pipe, SerialPipe):
        print(f"measured two-load/two-exec host round trip: {wall_ms:.2f} ms")
    print("axhost: FAST SWITCH PASS "
          "(two accelerator programs, one resident aXos/FPGA image, no synthesis)")


def main():
    parser = argparse.ArgumentParser(description="atomiX host-link driver")
    parser.add_argument("--demo", action="store_true",
                        help="run PING/INFO and a job against the selected role")
    parser.add_argument("--fast-switch", action="store_true",
                        help="load/run two GPU programs in one live session")
    parser.add_argument("--role", choices=sorted(BUILDERS), default="loopback",
                        help="which role the SoC profile selects")
    parser.add_argument("--image", help="aXos host-link hex image (simulation)")
    parser.add_argument("--config", help="SoC profile (simulation)")
    parser.add_argument("--boot-rom",
                        help="UART boot ROM hex image (boot simulation)")
    parser.add_argument("--upload-kernel", metavar="BIN",
                        help="upload this aXos binary before the requested action")
    parser.add_argument("--serial", metavar="TTY",
                        help="physical transport, e.g. /dev/ttyUSB1")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--max-cycles", type=int, default=800000)
    args = parser.parse_args()

    if args.serial:
        pipe = SerialPipe(args.serial, args.baud, args.timeout)
    else:
        if not args.config:
            parser.error("--config is required for simulation")
        if bool(args.boot_rom) != bool(args.upload_kernel):
            parser.error("simulation requires --boot-rom and --upload-kernel together")
        if not args.boot_rom and not args.image:
            parser.error("--image is required for direct-RAM simulation")
        pipe = SimPipe(args.image, args.config, args.max_cycles,
                       args.boot_rom, args.upload_kernel)
    if args.serial and args.upload_kernel:
        upload_kernel(pipe, args.upload_kernel)
    if args.demo:
        demo(pipe, args.role)
    elif args.fast_switch:
        fast_switch(pipe, args.baud)
    elif args.upload_kernel:
        if not args.serial:
            parser.error("boot simulation needs --demo or --fast-switch")
    else:
        parser.error("no action requested (try --demo or --fast-switch)")


if __name__ == "__main__":
    main()
