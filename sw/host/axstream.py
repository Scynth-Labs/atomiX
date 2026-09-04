#!/usr/bin/env python3
"""axstream — chunked host-link transfer for role.gpu-compute.

Drives a GPU job whose data is moved through the role window in frame-sized
pieces (docs/host-protocol.md, ops 0x15/0x16/0x17), so a job's size is bounded
by the accelerator's own memory rather than by what one request frame and one
kernel stack frame can hold.

    axstream.py --image <axos.hex> --config <soc.json> \
        --kernel-config <kernel.json>

This lives beside axhost.py rather than inside it deliberately. axhost.py is a
content-addressed artifact: seven records under research/live-fpga/ pin its
exact sha256, including physical Tang Primer evidence for thirty verified
executions and the deployment records built on them. Editing it would invalidate
those, and re-stamping their hashes would assert that a file which never ran on
the board produced the board's results. So the frame codec and the transports
are imported from it, unchanged, and the new protocol lives here.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from axhost import (
    ROLE_ID_GPU, ROOT, ST_OK, SimPipe, SerialPipe, OP_BYE, OP_GPU_LOAD,
    OP_INFO, gpu_load_payload, parse_responses, request, upload_kernel,
)
from gpu_programs import (
    GPU_ADD, GPU_ADDI, GPU_HALT, GPU_LDX, GPU_MULI, GPU_STX, GPU_TID, gpu_insn,
)

OP_GPU_WRITE = 0x15
OP_GPU_LAUNCH = 0x16
OP_GPU_READ = 0x17
ST_BAD_OP = 0x01
ST_BAD_LEN = 0x02


def _kernel_macro(name):
    """Read a bound from the header that defines it, so this driver cannot
    carry its own stale copy of a kernel limit. A rename fails here loudly
    rather than silently testing the wrong number."""
    for line in (ROOT / "sw/kernel/include/role.h").read_text().splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "#define" and parts[1] == name:
            return int(parts[2].rstrip("uU"))
    raise SystemExit(f"axstream: {name} not found in sw/kernel/include/role.h")


def setting(kernel_config, name, macro):
    """One capacity as the build saw it: the profile's setting if it declares
    one, otherwise the #ifndef default in role.h. Both come from their owning
    file, so no limit here can be a stale copy of a kernel number."""
    if kernel_config:
        settings = json.loads(Path(kernel_config).read_text()).get("settings", {})
        if name in settings:
            return settings[name]
    return _kernel_macro(macro)

# Offsets to probe for the role-memory bound. Every role memory in the tree is
# a power of two, so bracketing each power finds the exact edge: the largest
# accepted offset is capacity - 1. The ladder is fixed rather than adaptive
# because the simulation transport is a batch -- every request is written
# before the model runs -- and because a test that asks the device where its
# edge is cannot accidentally repeat the number it is checking.
def declared_capacity(kernel_config):
    """The role memory this profile declared, or None if it declared none --
    in which case the kernel has no chunked ops at all and must refuse them.
    There is deliberately no default to fall back on."""
    if not kernel_config:
        return None
    settings = json.loads(Path(kernel_config).read_text()).get("settings", {})
    return settings.get("role_data_words")


PROBE_OFFSETS = [o for k in range(6, 15) for o in (2 ** k - 1, 2 ** k)]


def write_payload(offset, words):
    return struct.pack("<HH", offset, len(words)) + b"".join(
        struct.pack("<I", w & 0xFFFFFFFF) for w in words)


def read_payload(offset, nwords):
    return struct.pack("<HH", offset, nwords)



def chunk_writes(offset, words, chunk):
    return b"".join(
        request(OP_GPU_WRITE, write_payload(offset + i, words[i:i + chunk]))
        for i in range(0, len(words), chunk))


def chunk_reads(offset, nwords, chunk):
    return b"".join(
        request(OP_GPU_READ, read_payload(offset + i, min(chunk, nwords - i)))
        for i in range(0, nwords, chunk))


def probe_capacity(frames):
    """Largest accepted single-word offset, + 1. Reads the device's own answers
    rather than a constant, so this test cannot drift from the kernel bound."""
    accepted = [off for off, (status, _) in zip(PROBE_OFFSETS, frames)
                if status == ST_OK]
    if not accepted:
        raise SystemExit("axstream: role rejected every probe offset")
    return max(accepted) + 1


def build_job(capacity, staged):
    """SIMT saxpy sized from the capacity the device reported.

    Three arrays share the role memory, so the thread count is a third of it,
    capped so a large role does not turn this into a long simulation. It must
    also land above the staged bound, or the staged encoding could have carried
    the job and the run would prove nothing."""
    n = min(capacity // 3, 256)
    if 3 * n <= staged:
        raise SystemExit(
            f"axstream: a {3 * n}-word job fits the {staged}-word staged "
            f"encoding; this profile cannot demonstrate chunked transfer")
    base_a, base_b, base_c = 0, n, 2 * n
    a = [i + 1 for i in range(n)]
    b = [100 + 2 * i for i in range(n)]
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
    ref = [(3 * a[i] + b[i]) & 0xFFFFFFFF for i in range(n)]
    return n, base_a, base_b, base_c, a, b, prog, ref


def expect_refused(pipe):
    """A build whose profile declared no role memory has no chunked ops. It
    must say so rather than act on a capacity nobody gave it."""
    session = (request(OP_GPU_WRITE, write_payload(0, [0])) +
               request(OP_GPU_LAUNCH, struct.pack("<H", 1)) +
               request(OP_GPU_READ, read_payload(0, 1)))
    frames = parse_responses(pipe.exchange(session + request(OP_BYE), 3))
    if len(frames) < 3:
        raise SystemExit(f"axstream: expected 3 responses, got {len(frames)}")
    for name, (status, _) in zip(("GPU_WRITE", "GPU_LAUNCH", "GPU_READ"), frames):
        if status != ST_BAD_OP:
            raise SystemExit(
                f"axstream: {name} returned status {status} on a build that "
                f"declared no role memory; want BAD_OP ({ST_BAD_OP})")
    print("REFUSED -> no role_data_words declared, all three chunked ops "
          "answer BAD_OP")
    print("axstream: omit-when-undeclared PASS (no capacity was invented)")


def stream(pipe, kernel_config=None):
    """Drive one GPU job whose data is moved in frame-sized chunks.

    The job deliberately exceeds what a single request frame can carry, which
    is the whole point: the staged GPU_RUN/GPU_EXEC encoding bounds a job by
    the frame and by the kernel stack, and this one is bounded only by the
    role's own memory."""
    declared = declared_capacity(kernel_config)
    if declared is None:
        expect_refused(pipe)
        return

    payload = setting(kernel_config, "role_max_payload", "ROLE_MAX_PAYLOAD")
    staged = setting(kernel_config, "role_staged_words", "ROLE_STAGED_WORDS")
    frame_chunk = (payload - 4) // 4
    chunk = min(frame_chunk, declared)
    print(f"CAPS  -> role_data_words={declared} role_max_payload={payload} "
          f"role_staged_words={staged}; chunk cap {frame_chunk} words")

    probes = b"".join(
        request(OP_GPU_WRITE, write_payload(off, [0])) for off in PROBE_OFFSETS)
    frames = parse_responses(pipe.exchange(
        request(OP_INFO) + probes + request(OP_BYE), 1 + len(PROBE_OFFSETS)))
    if len(frames) < 1 + len(PROBE_OFFSETS):
        raise SystemExit(
            f"axstream: expected {1 + len(PROBE_OFFSETS)} probe responses, "
            f"got {len(frames)}")
    info_status, info_payload = frames[0]
    if info_status != ST_OK or len(info_payload) != 8:
        raise SystemExit(f"axstream: bad INFO response {frames[0]!r}")
    role_id, _ = struct.unpack("<II", info_payload)
    if role_id != ROLE_ID_GPU:
        raise SystemExit(
            f"axstream: needs role.gpu-compute, got 0x{role_id:08x}")

    capacity = probe_capacity(frames[1:])
    if capacity != declared:
        raise SystemExit(
            f"axstream: device accepts {capacity} words but this kernel "
            f"profile declares {declared}; the bound is not the one the "
            f"build was given")
    print(f"PROBE -> role memory {capacity} words "
          f"(device-reported, matches the profile)")

    n, base_a, base_b, base_c, a, b, prog, ref = build_job(capacity, staged)
    ndata = base_c + n

    # Both bounds from both sides. The role-memory rejections matter because a
    # role answers an access past its buffer with a bus error, which is a store
    # access fault in supervisor mode; the frame rejection matters because a
    # chunk longer than the staging buffer never arrived intact.
    edges = [
        ("last word", OP_GPU_WRITE, write_payload(capacity - 1, [0]), ST_OK),
        ("one past the end", OP_GPU_WRITE, write_payload(capacity, [0]),
         ST_BAD_LEN),
        ("straddling the end", OP_GPU_WRITE,
         write_payload(capacity - 2, [0] * 3), ST_BAD_LEN),
        ("read past the end", OP_GPU_READ, read_payload(capacity, 1),
         ST_BAD_LEN),
        ("chunk over the frame cap", OP_GPU_WRITE,
         write_payload(0, [0] * (frame_chunk + 1)), ST_BAD_LEN),
    ]
    session = b"".join(request(op, payload_) for _, op, payload_, _ in edges)
    session += request(OP_GPU_LOAD, gpu_load_payload(prog))
    session += chunk_writes(base_a, a, chunk) + chunk_writes(base_b, b, chunk)
    session += request(OP_GPU_LAUNCH, struct.pack("<H", n))
    session += chunk_reads(base_c, n, chunk)
    per_array = (n + chunk - 1) // chunk
    nwrite, nread = 2 * per_array, per_array
    expected = len(edges) + 1 + nwrite + 1 + nread
    frames = parse_responses(pipe.exchange(session + request(OP_BYE), expected))
    if len(frames) < expected:
        raise SystemExit(
            f"axstream: expected {expected} responses, got {len(frames)}")

    for i, (name, _, _, want_status) in enumerate(edges):
        got = frames[i][0]
        if got != want_status:
            raise SystemExit(
                f"axstream: bound check {name!r} returned status {got}, "
                f"want {want_status}")
    print(f"BOUND -> {capacity - 1} accepted, {capacity} rejected, "
          f"{capacity - 2}+3 rejected, read at {capacity} rejected, "
          f"{frame_chunk + 1}-word chunk rejected")

    at = len(edges)
    if frames[at][0] != ST_OK:
        raise SystemExit(f"axstream: GPU_LOAD status {frames[at][0]}")
    for i in range(at + 1, at + 1 + nwrite):
        if frames[i][0] != ST_OK:
            raise SystemExit(f"axstream: chunk write {i} status {frames[i][0]}")
    launch_status, launch_payload = frames[at + 1 + nwrite]
    if launch_status != ST_OK or len(launch_payload) != 4:
        raise SystemExit(
            f"axstream: GPU_LAUNCH response {frames[at + 1 + nwrite]!r}")
    cycles = struct.unpack("<I", launch_payload)[0]

    got = []
    for i in range(at + 2 + nwrite, at + 2 + nwrite + nread):
        status, chunk_payload = frames[i]
        if status != ST_OK:
            raise SystemExit(f"axstream: chunk read {i} status {status}")
        got.extend(struct.unpack(f"<{len(chunk_payload) // 4}I", chunk_payload))
    if got != ref:
        bad = next(j for j in range(len(ref)) if got[j] != ref[j])
        raise SystemExit(f"axstream: streamed C[{bad}] = {got[bad]} != {ref[bad]}")
    print(f"JOB   -> SIMT saxpy over {n} threads, {ndata} words resident, "
          f"moved in {nwrite + nread} chunks of <= {chunk}; "
          f"launch {cycles} cycles")
    print(f"axstream: chunked-transfer PASS (job of {ndata} words, staged "
          f"encoding caps at {staged}, one frame carries {frame_chunk})")


def main():
    parser = argparse.ArgumentParser(
        description="chunked host-link transfer for role.gpu-compute")
    parser.add_argument("--image", help="aXos host-link hex image (simulation)")
    parser.add_argument("--config", help="SoC profile (simulation)")
    parser.add_argument("--kernel-config",
                        help="kernel profile the image was built from; the "
                             "device bound is checked against what it declares")
    parser.add_argument("--boot-rom",
                        help="UART boot ROM hex image (boot simulation)")
    parser.add_argument("--upload-kernel", metavar="BIN",
                        help="upload this aXos binary before streaming")
    parser.add_argument("--serial", metavar="TTY",
                        help="physical transport, e.g. /dev/ttyUSB1")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--max-cycles", type=int, default=4000000)
    args = parser.parse_args()

    if args.serial:
        pipe = SerialPipe(args.serial, args.baud, args.timeout)
        if args.upload_kernel:
            upload_kernel(pipe, args.upload_kernel)
    else:
        if not args.config:
            parser.error("--config is required for simulation")
        if bool(args.boot_rom) != bool(args.upload_kernel):
            parser.error("simulation requires --boot-rom and --upload-kernel together")
        if not args.boot_rom and not args.image:
            parser.error("--image is required for direct-RAM simulation")
        pipe = SimPipe(args.image, args.config, args.max_cycles,
                       args.boot_rom, args.upload_kernel)
    stream(pipe, args.kernel_config)


if __name__ == "__main__":
    main()
