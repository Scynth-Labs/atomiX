# aX host-link protocol (v0)

The wire contract between `axhost` on the host PC and the aXos host-link
service on the FPGA shell (DESIGN.md §3.3).  This is the **base**: a minimal,
functionally complete request/response protocol that proves the whole
host → shell → role control plane end-to-end. Per-role job submission and
chunked buffer transfer are implemented; asynchronous completion, flow control,
and CRC can layer on this frame format without breaking it.

## Transport

The protocol is a byte stream; it does not care what carries it.

- **Base (today):** the shell's console byte pipe.  In simulation this is the
  Verilator harness UART — `axhost` writes request bytes to `UART_INPUT_FILE`
  and reads response bytes from the model's stdout (the "virtual pipe" backend).
- **Hardware:** `axhost --serial /dev/ttyUSBx` opens the board's existing
  USB-UART directly (including a WSL-attached device). The host-link personality
  owns that UART, so it replaces the human console in this image. A future
  second byte pipe can make the two concurrent without changing the frames.

In the base, aXos runs a **host-link personality** (built with `HOSTLINK=1`)
that speaks this protocol instead of the interactive shell.  The two are
unified into one concurrent image once the dedicated channel exists.

Before this protocol starts, kernel profiles reset into the immutable UART ROM.
The host sends `"AXK1" · length(u32) · crc32(u32) · kernel[length]`; the ROM
answers `"AXOK" · length(u32)`, executes `fence.i`, and starts aXos. `"AXER" ·
code(u32)` reports a bad bound or CRC and leaves the ROM ready for another
upload. After page-table, allocator, and role initialization, aXos sends
`"AXRD"`. The host must not send request frames between `AXOK` and `AXRD`:
`AXOK` proves only that the ROM accepted the bytes, while `AXRD` is the request-
ready boundary. This boot envelope is transport-level and independent of the
aXos request frames below.

## Frames

All multi-byte integers are little-endian.

```
Request   A5 | op(1) | len(2) | payload(len)
Response  5A | status(1) | len(2) | payload(len)
```

- `A5` / `5A` are the request/response sync bytes.  A receiver resynchronizes
  by scanning for its sync byte, so a corrupt or partial frame cannot desync
  the stream permanently.
- `status` is `0` on success; nonzero is an error code (below).
- `len` is the payload byte count, `0..65535` (the base caps `ROLE_RUN` data at
  `HOSTLINK_MAX_WORDS` words).

## Opcodes (v0)

| op   | name       | request payload                                   | ok response payload             |
|------|------------|---------------------------------------------------|---------------------------------|
| 0x01 | `PING`     | none                                              | 4 bytes: `61 58 48 4C` (`aXHL`)  |
| 0x02 | `INFO`     | none                                              | `role_id`(u32) · `version`(u32)  |
| 0x10 | `ROLE_RUN` | `words`(u16) · `words`×u32 input                  | `words`×u32 result              |
| 0x11 | `TPU_GEMM` | `m`(u8) · `ctrl`(u8) · `W`[64 i8] · `A`[8·m i8]   | `C`[m·8 i32]                    |
| 0x12 | `GPU_RUN`  | `nthreads`(u16) · `ninsn`(u16) · `ndata`(u16) · `prog`[ninsn u32] · `data`[ndata u32] | `data`[ndata u32] |
| 0x13 | `GPU_LOAD` | `ninsn`(u16) · `prog`[ninsn u32]                  | `load_cycles`(u32)              |
| 0x14 | `GPU_EXEC` | `nthreads`(u16) · `ndata`(u16) · `data`[ndata u32] | `exec_cycles`(u32) · `data`[ndata u32] |
| 0x15 | `GPU_WRITE` | `offset`(u16 words) · `nwords`(u16) · `words`[nwords u32] | none |
| 0x16 | `GPU_LAUNCH` | `nthreads`(u16)                                  | `exec_cycles`(u32)              |
| 0x17 | `GPU_READ` | `offset`(u16 words) · `nwords`(u16)               | `words`[nwords u32]             |
| 0x7F | `BYE`      | none                                              | none (then the session ends)    |

- `PING` proves the transport and framing round-trip.
- `INFO` returns what `ROLE_ID`/`VERSION` read through the role window
  (`role_id == 0` means no role is present).  It tells the host which of the
  role-specific ops below apply.
- `ROLE_RUN` targets `role.loopback` (the contract-proof role): aXos copies the
  input words through the engine and returns them.  Its role in the base is to
  exercise the whole path with a trivially checkable result.
- `TPU_GEMM` targets `role.tpu-lite`: aXos loads the K=8 weight tile and
  `m` activation rows, latches `ctrl` (`0x1` ReLU, `0x2` accumulate), runs the
  folded 24-MAC GEMM, and returns the `m × 8` int32 result tile.
- `GPU_RUN` targets `role.gpu-compute`: aXos uploads a straight-line kernel and
  a flat data buffer, launches `nthreads` SIMT lanes over the program, and
  returns the data buffer read back.
- `GPU_LOAD`/`GPU_EXEC` split that operation at the reconfiguration boundary.
  `GPU_LOAD` replaces only the engine's microcode while the FPGA image and aXos
  remain resident. Repeated `GPU_EXEC` calls reuse it. The reported cycle counts
  cover kernel-side MMIO work, accelerator execution, and checked readback as
  applicable; UART transfer time is measured by the host.
- `GPU_WRITE`/`GPU_LAUNCH`/`GPU_READ` are the same job as `GPU_EXEC`, split at
  the two places it already had a seam.  They move the data buffer in
  frame-sized pieces straight through the role window, so a job's size stops
  being bounded by what one request frame can carry and by what the kernel can
  hold: `GPU_EXEC` marshals the whole buffer through arrays on the kernel's
  stack, which is why its `ndata` cap is 200 words on every profile, while a
  streamed job is bounded only by the role's own memory.  A session is
  `GPU_LOAD`, then `GPU_WRITE` per chunk, then `GPU_LAUNCH`, then `GPU_READ`
  per chunk.  `GPU_LAUNCH` is still synchronous; decoupling transfer from
  execution is what a later asynchronous completion would build on, not the
  asynchronous completion itself.
- Chunks are bounded from both ends.  A chunk must fit the frame that carried
  it, `nwords <= (role_max_payload - 4) / 4`, and it must land inside the role's
  memory, `offset + nwords <= role_data_words`.  Anything past either is
  `BAD_LEN` and never reaches the device, which matters because a role answers
  an access past its memory with a bus error — a store access fault in
  supervisor mode.  Both settings are described below.
- Each role op returns `NO_ROLE` if the shell does not currently hold that role.
- `BYE` lets the host end the session cleanly; aXos acknowledges, then halts.

Per-role ops keep the accelerator's job encoding on the host side (`axhost` and
its role libraries); aXos only marshals the described buffers into the role
window and runs the shared doorbell/status cycle.

## Status codes

| code | meaning                                            |
|------|----------------------------------------------------|
| 0x00 | `OK`                                                |
| 0x01 | `BAD_OP` — unknown opcode                            |
| 0x02 | `BAD_LEN` — payload length invalid for the opcode    |
| 0x03 | `NO_ROLE` — op needs a role the shell does not have  |
| 0x04 | `DEVICE` — the accelerator did not complete          |

## The three capacities, and who owns them

All three are kernel profile settings (`tools/configure.py` `SETTINGS`) rather
than component parameters, because none of them belongs to one component.  The
kernel is not built against a role at all: `KERNEL_CONFIG` selects software
components, the role comes from the hardware profile, and one kernel image
discovers whatever role is present at runtime.  The kernel profile therefore
carries its assumptions about the hardware it will meet, the way `ram_bytes`
already does.

| setting | bounds | default | what it sizes |
|---|---|---|---|
| `role_max_payload` | 64..65535 | 1280 | the staging buffer every front end marshals an encoded job through |
| `role_staged_words` | 8..4096 | 200 | the whole-job-in-one-frame arrays on `role_execute`'s stack |
| `role_data_words` | 64..15360 | *none* | the role global memory the chunked ops may address |

`role_max_payload` was a `syscall.linux-compat` parameter until three paths came
to share it — the syscall component's `role_submit`, the role dispatcher, and
the host-link service all stage the same encoded job through the same
`role_execute`, and the host-link personality contains no syscalls at all, so a
syscall-component parameter was sizing a buffer in a build that does not contain
that component's reason for existing.

`role_staged_words` bounds the kernel stack rather than the accelerator, which
is why it stays distinct from `role_data_words`: they answer different
questions and a profile may move one without the other.

`role_data_words` has **no default**, deliberately.  A profile that does not
declare it does not get the chunked ops: they are not compiled and the service
answers `BAD_OP`.  A capacity the build was never told is not one to guess, and
guessing high is the dangerous direction — `role.gpu-tpu` presents the
gpu-compute engine at `ROLE_ID` `"GPUC"` and the original offsets, so this
driver runs against it over a quarter of `role.gpu-compute`'s memory, and a role
answers an access past its buffer with a bus error.  Its upper bound is the
window's geometry rather than a round number: the role window is 64 KiB and the
data region starts at `0x1000`, so `(0x10000 - 0x1000) / 4` words is the most
any role can decode.

Two relations hold between them, asserted in `sw/kernel/include/role.h` beside
the definitions because only that header sees both: the staged job must fit the
staging buffer, and it must fit the role memory the profile declared.

## Authority

`sw/kernel/include/hostlink.h` (aXos side) and `sw/host/axhost.py` (host side)
implement this document; keep both in step with it.  The chunked ops are driven
by `sw/host/axstream.py`, which imports the frame codec and both transports
from `axhost.py` rather than extending it: `axhost.py` is a content-addressed
artifact and seven records under `research/live-fpga/` pin its exact hash,
including physical Tang Primer evidence.  A new host-side protocol therefore
gets a new module; editing the pinned one would invalidate those records, and
re-stamping their hashes would claim that a file which never ran on the board
produced the board's results.  Evidence:
`make -C sw/kernel check-hostlink` runs `axhost` against the simulated shell
with `role.loopback` and checks every response.
`make -C sw/kernel check-hostlink-stream` covers the chunked ops against
`role.gpu-compute` in three legs, all on the same SoC profile so the hardware
stays constant at 4096 role words and only the kernel profile varies: 4096
declared reaches all of it, 1024 declared (with the payload and staged caps
also moved off their defaults) bounds at exactly 1024 on that same 4096-word
hardware, and a profile declaring nothing answers `BAD_OP`.  The host derives
every limit from the profile and from the header's defaults and probes the
device for its own edge, so no limit is written down in the test. Role job parsing and MMIO
marshaling live in the same checked kernel dispatcher used by the userspace
`role_submit` ABI, so the local and remote encodings cannot drift apart.
`make -C sw/kernel check-primer-runtime` additionally loads and executes two
different GPU programs in one resident 32 KiB aXos/RTL session.
