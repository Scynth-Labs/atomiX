# aX host-link protocol (v0)

The wire contract between `axhost` on the host PC and the aXos host-link
service on the FPGA shell (DESIGN.md §3.3).  This is the **base**: a minimal,
functionally complete request/response protocol that proves the whole
host → shell → role control plane end-to-end. Per-role job submission is
implemented; streaming buffers, asynchronous completion, flow control, and CRC
can layer on this frame format without breaking it.

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
upload. This boot envelope is transport-level and independent of the aXos
request frames below.

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

## Authority

`sw/kernel/include/hostlink.h` (aXos side) and `sw/host/axhost.py` (host side)
implement this document; keep both in step with it.  Evidence:
`make -C sw/kernel check-hostlink` runs `axhost` against the simulated shell
with `role.loopback` and checks every response. Role job parsing and MMIO
marshaling live in the same checked kernel dispatcher used by the userspace
`role_submit` ABI, so the local and remote encodings cannot drift apart.
`make -C sw/kernel check-primer-runtime` additionally loads and executes two
different GPU programs in one resident 32 KiB aXos/RTL session.
