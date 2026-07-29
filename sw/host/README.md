# sw/host/ — axhost host-side driver

The software that runs on your PC and makes the FPGA a managed accelerator
card.  It speaks the framed aX host-link protocol
([docs/host-protocol.md](../../docs/host-protocol.md)) to the aXos host-link
service on the shell — never role internals.

## What exists (base)

- [`axhost.py`](axhost.py) — the host driver: immutable-ROM kernel upload,
  host-link frame codec, a Verilator virtual pipe, and a physical USB-serial
  backend. It runs PING/INFO/jobs, and can load and execute two different GPU
  programs without rebuilding the FPGA. Evidence:
  `make -C sw/kernel check-hostlink check-uartboot check-primer-runtime`.

The aXos side is the host-link personality built with `HOSTLINK=1`
([sw/kernel/hostlink.c](../kernel/hostlink.c)), which dispatches frames to the
in-kernel role driver ([sw/kernel/role.c](../kernel/role.c)).

Upload and start a kernel on an attached runtime image:

```bash
python3 sw/host/axhost.py \
  --upload-kernel sw/kernel/build/primer-runtime/axos_boot.bin \
  --serial /dev/ttyUSB1 --baud 921600
```

Add `--fast-switch` to immediately run the two-program GPU regression.

## What layers on next

- Per-role client libraries (e.g. `libaxtpu`: a matmul API that marshals
  tensors into a TPU-lite descriptor) above the frame codec.
- New opcodes on the existing frame format: per-role job submission, buffer
  read/write and streaming, asynchronous completion, and cached-bitstream
  selection for physical-datapath switching.
- A dedicated second byte pipe so the interactive console and host protocol can
  operate concurrently instead of selecting one aXos UART personality.

Design rule: `axhost` knows the **shell protocol only** — never role internals.
Role knowledge lives in aXos and in the per-role libraries.  Userspace only
(plain USB-serial); no kernel module unless PCIe ever happens.
