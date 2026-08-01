# sim/web/ — the machine in a browser tab

The same Verilated SoC that `make sim` runs, compiled to WebAssembly, so aXos
can be booted with no toolchain, no FPGA, and nothing installed.

This tier is **optional and load-bearing for nothing**. The ISS, Verilator
simulation, kernel checks, formal flow, and FPGA flow all work without it, and
no evidence claim rests on it. If `emcc` is absent the targets here are simply
unavailable, exactly as the FPGA targets are without the OSS CAD Suite.

## Build and run

```bash
./tools/web.sh                       # from the repository root
```

One command from a plain shell: it sources the Emscripten SDK, prefers a
Verilator the suite is green on, builds the aXos payload if it is missing, runs
the headless check, finds a free port, and opens the page.
`--check-only`, `--port N`, `--no-open`, `--config`, and `--payload` are the
knobs; `--help` lists them.

The steps are ordinary targets underneath, for anyone who wants them separately:

```bash
source ~/emsdk/emsdk_env.sh          # see docs/toolchain.md
make -C sim/web check                # headless boot under Node
make -C sim/web serve PORT=8000      # then open http://localhost:8000/
```

`serve` is a plain static file server; any other one works. The page fetches
the WASM module, so `file://` will not do.

The bundle lives at one fixed path (`public/axsoc.js`) because the page loads it
by name, so it is keyed on the profile and payload with a stamp file. Without
that, switching profiles leaves a newer bundle built from the *other* selection,
which make would call up to date — serving the wrong machine under the right
label is the one failure this directory must not have.

## What is and is not shared with the native runner

Shared, and deliberately so: the RTL, every elaboration parameter, and the
per-cycle body of the machine — clocking, the UART handshake, and the SPI
sampling edge — all come from
[components/harness/common/soc_machine.h](../../components/harness/common/soc_machine.h),
which the batch runner, the interactive console session, and this driver all
use unmodified. A cycle count read off the page is the cycle count a local
`make sim` reports, because it is produced by the same code advancing the same
model.

Not shared: the loop around it. The native runner owns its loop and blocks on
stdin; a browser permits neither, so
[tb_soc_wasm.cpp](tb_soc_wasm.cpp) inverts control and exports a small C API —
boot, queue an input byte, run a bounded slice, drain output, read the cycle
count. Everything specific to a browser (frame scheduling, terminal rendering,
key handling) lives in JavaScript and touches nothing but that API.

## Runtime payload selection

`RAM_INIT_FILE` is compiled into the model at elaboration, which normally means
changing the program means rebuilding it. Here the compiled-in path is a
*virtual* one (`/payload.hex`) and the model's `$readmemh` reads it out of the
in-memory filesystem when `ax_boot()` constructs the machine. The caller writes
the image there first, so one compiled machine boots whichever payload is
staged beside it:

```bash
make -C sim/web build PAYLOAD="$PWD/sw/baremetal/build/hello.hex"
```

A developer's absolute home directory is therefore never baked into a page.

## Measurements

`make -C sim/web bench` builds both machines from the same profile and times
them on the same host, which is the only fair comparison — a ratio against a
baseline recorded elsewhere conflates "WebAssembly is slower" with "this
machine is slower", and those have opposite consequences.

Recorded on the WSL2 host in [docs/dependencies.md](../../docs/dependencies.md),
profile `sim-role-loopback`, payload `sw/kernel/build/axos_boot.hex`, over three
runs:

| | cycles/s | boot to `aXos>` |
|---|---|---|
| Native (Verilator 4.038) | 1.0–1.1M | 27,509 cycles / 26–27 ms |
| WebAssembly (Node) | 0.86–0.90M | 27,509 cycles / 25–35 ms |

Same cycle count, as it must be — it is the same RTL running the same payload.
The absolute rates move with whatever else the host is doing; the ratio does
not, and it is **0.94–1.37x wall-clock** -- effectively parity. That is why
the numbers to quote are the cycle count and the ratio rather than a single
millisecond figure. The whole bundle is about 374 KB — 177 KB of WASM machine,
61 KB of Emscripten glue, 117 KB of aXos payload, and 18 KB of page.

The cycle count is a property of the RTL and the payload, so it moves when
either does — `sw/kernel` built with a non-default `KERNEL_CONFIG` boots in a
different number of cycles. `boot.mjs` says so out loud when the count differs
from the one recorded in the checklist rather than quietly reporting a number
that no longer matches the claim beside it.

`make -C sim/web check` is the self-check behind those numbers: it boots
headless, waits for the shell prompt, then runs the shell's `role` twice and
requires `irq=1` followed by `irq=2`. Two boots would report `irq=1` twice, so
that is what proves the page is one continuous machine rather than a batch run
per command.

## Files

| | |
|---|---|
| `tb_soc_wasm.cpp` | the C API the page drives the machine through |
| `boot.mjs` | headless boot, self-check, and same-host benchmark |
| `public/index.html`, `app.js`, `style.css` | the page; tracked sources |
| `public/axsoc.js`, `axsoc.wasm`, `payload.hex` | build products, not tracked |

## Known limits

- Single machine per page. Booting several profiles side by side is the next
  step in [the checklist](../../docs/design-checklist.md), not something this
  page does yet.
- The build tracks Verilator 4.038, the version the rest of the suite is green
  on. Verilator 5 currently fails the same way it fails for `sim/soc`.
- At an idle prompt the machine is genuinely stopped, not throttled. `wfi`
  parks the hart and the console is interrupt-driven, so `ax_run` returns as
  soon as the CPU parks and the cycle counter holds still until you type. The
  page reads that from `ax_cpu_idle()` -- the machine's own signal -- and waits
  on a timer rather than asking for 60 animation frames a second to discover a
  hart is still asleep. The same interaction cost 10.1M cycles before that work
  and costs 52,672 now.
- One window is still text-matched: the console driver only parks once the
  console interrupt has delivered a byte, so between boot and your first
  keystroke the shell really is spinning, and the page falls back to
  recognising the prompt to throttle. After the first key, `ax_cpu_idle()` is
  authoritative.
