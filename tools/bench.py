#!/usr/bin/env python3
"""Benchmark the CPU cores and accelerator roles.

The sweep needs a profile per (core, role) combination, but those combinations
are measurement fixtures rather than supported configurations, so they are
generated into a scratch directory instead of being checked into configs/.
The catalog keeps only the profiles a suite actually runs.

usage:
  tools/bench.py cpu      IPC and cycle counts per core
  tools/bench.py gpu      GPU kernel cycles per role tier
  tools/bench.py tpu      TPU GEMM cycles versus the host CPU
  tools/bench.py tang     exact Nano/Primer max-profile end-to-end comparison
  tools/bench.py render   render workload vs cache size and divider latency
  tools/bench.py all      all five (default)

Requires the baremetal images: make -C sw/baremetal images
"""
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(os.path.join(__file__, "..")))
CONFIGS = os.path.join(ROOT, "configs")
IMAGES = os.path.join(ROOT, "sw", "baremetal", "build")

# Cores to compare.  ax2 is one component, so its entries are parameter
# settings rather than separate components -- which is the point: the sweep
# shows what each knob is worth instead of asserting that three tiers differ.
CPUS = [
    ("core.minimal", {}, "multi-cycle, no cache"),
    ("core.pipeline5", {}, "5-stage reference"),
    ("core.ax2", {"issue_width": 1, "icache_kb": 1, "btb_entries": 0},
     "ax2 1-wide, 1K I$, no BTB"),
    ("core.ax2", {"issue_width": 1, "icache_kb": 2, "btb_entries": 32},
     "ax2 1-wide, 2K I$, BTB 32"),
    ("core.ax2", {"issue_width": 2, "icache_kb": 2, "btb_entries": 0},
     "ax2 2-wide, 2K I$, no BTB"),
    ("core.ax2", {"issue_width": 2, "icache_kb": 2, "btb_entries": 32},
     "ax2 2-wide, 2K I$, BTB 32  (default)"),
    ("core.ax2", {"issue_width": 2, "icache_kb": 2, "btb_entries": 64},
     "ax2 2-wide, 2K I$, BTB 64  (GW5A-25 max)"),
    ("core.ax2", {"issue_width": 2, "icache_kb": 8, "btb_entries": 128},
     "ax2 2-wide, 8K I$, BTB 128"),
]

# GPU roles, old family then new, so the banking effect is visible.
# Likewise for the roles: both engines are single tunable components now.
GPUS = [
    ("role.gpu-compute", {"lanes": 4}, "gpu-compute 4 lanes, 1 port", "gpu"),
    ("role.gpu-compute", {"lanes": 8}, "gpu-compute 8 lanes, 1 port", "gpu"),
    ("role.gpu-compute", {"lanes": 16}, "gpu-compute 16 lanes, 1 port", "gpu"),
    ("role.gpu1", {"lanes": 4, "banks": 4}, "gpu1 4 lanes / 4 banks", "gpu1"),
    ("role.gpu1", {"lanes": 8, "banks": 8}, "gpu1 8 lanes / 8 banks", "gpu1"),
    ("role.gpu1", {"lanes": 16, "banks": 4}, "gpu1 16 lanes / 4 banks", "gpu1"),
    ("role.gpu1", {"lanes": 16, "banks": 16}, "gpu1 16 lanes / 16 banks", "gpu1"),
    ("role.gpu1", {"lanes": 32, "banks": 32}, "gpu1 32 lanes / 32 banks", "gpu1"),
]


def make_profile(workdir, name, core, role=None, params=None):
    base = json.load(open(os.path.join(CONFIGS, "sim-hello.json")))
    base["name"] = name
    base["components"]["core"] = core
    base["components"].pop("software", None)
    if role:
        base["components"]["role"] = role
    if params:
        base["parameters"] = params
    path = os.path.join(workdir, name + ".json")
    json.dump(base, open(path, "w"), indent=2)
    return path


def make_board_sim_profile(workdir, source):
    """Turn an FPGA profile into an exact board-independent sim fixture."""
    profile = json.load(open(os.path.join(CONFIGS, source)))
    profile["name"] = "sim-" + profile["name"]
    profile["components"]["board"] = "board.sim"
    profile["components"]["harness"] = "harness.verilator-soc"
    profile["components"].pop("software", None)
    path = os.path.join(workdir, profile["name"] + ".json")
    json.dump(profile, open(path, "w"), indent=2)
    return path


def slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")


def run(profile, image, max_cycles):
    image_path = image if os.path.isabs(image) else os.path.join(IMAGES, image)
    out = subprocess.run(
        ["make", "-s", "-C", os.path.join(ROOT, "sim", "soc"), "run",
         "COMPONENT_CONFIG=" + profile,
         "RAM_INIT_FILE=" + image_path,
         "RESET_PC=0x80000000", "MAX_CYCLES=%d" % max_cycles],
        capture_output=True, text=True)
    return out.stdout + out.stderr


def bench_cpu(workdir):
    print("\n== CPU: retired instructions per cycle (higher is better) ==\n")
    rows = []
    for core, params, note in CPUS:
        prof = make_profile(workdir, "bench-" + slug(note), core,
                            params={"core": params} if params else None)
        text = run(prof, "cpu_perf.hex", 2000000)
        ipc = dict(re.findall(r"(\w+)\s*: insns=\d+ cycles=\d+ ipc_x100=(\d+)", text))
        measured = re.search(r"cpu_perf measured: cycles=(\d+)", text)
        # Older images did not emit the workload-only aggregate. Keep the
        # whole-program counter as a compatibility fallback, but prefer the
        # stable number that excludes UART and setup overhead.
        if not measured:
            measured = re.search(r"\[soc\] exit 0 \(cycles=(\d+)\)", text)
        if not ipc or not measured:
            print("  %-38s FAILED TO RUN" % note)
            continue
        rows.append((note, ipc, int(measured.group(1))))

    hdr = ("configuration", "alu", "chain", "branch", "memcpy", "mixed",
           "measured cyc", "vs minimal")
    print("  %-38s %6s %6s %7s %7s %6s %11s %11s" % hdr)
    print("  " + "-" * 98)
    baseline = rows[0][2] if rows else 1
    for note, ipc, total in rows:
        print("  %-38s %6s %6s %7s %7s %6s %11d %10.2fx" % (
            note,
            fmt(ipc.get("alu")), fmt(ipc.get("chain")), fmt(ipc.get("branch")),
            fmt(ipc.get("memcpy")), fmt(ipc.get("mixed")),
            total, baseline / total))


def fmt(x):
    return "%.2f" % (int(x) / 100.0) if x else "-"


def bench_gpu(workdir):
    print("\n== GPU: saxpy kernel cycles, 50 threads (lower is better) ==\n")
    rows = []
    for role, params, note, image in GPUS:
        prof = make_profile(workdir, "bench-" + slug(note), "core.ax2", role,
                            params={"role": params})
        text = run(prof, image + ".hex", 1500000)
        m = re.search(r"(?:gpu|gpu1) saxpy cycles: 0x([0-9a-f]+)", text)
        ok = "PASS" in text
        rows.append((note, int(m.group(1), 16) if m else None, ok))

    print("  %-34s %12s %10s" % ("configuration", "saxpy cyc", "speedup"))
    print("  " + "-" * 60)
    base = next((c for n, c, ok in rows if "gpu-compute 8" in n and c), None)
    for note, cyc, ok in rows:
        if cyc is None:
            print("  %-34s %12s %10s" % (note, "FAILED", "-"))
            continue
        sp = "%.2fx" % (base / cyc) if base else "-"
        print("  %-34s %12d %10s%s" % (note, cyc, sp, "" if ok else "  (FAILED)"))
    print("\n  speedup is relative to role.gpu-compute (8 lanes, single port).")


def bench_tpu():
    print("\n== TPU: 12x8x8 int8 GEMM cycles (lower is better) ==\n")
    profile = os.path.join(CONFIGS, "sim-tpu-lite.json")
    text = run(profile, "tpu.hex", 500000)
    accel = re.search(r"tpu gemm cycles: 0x([0-9a-f]+)", text)
    host = re.search(r"cpu gemm cycles: 0x([0-9a-f]+)", text)
    if not accel or not host or "role tpu-lite: PASS" not in text:
        print("  FAILED TO RUN")
        return
    accel_cycles = int(accel.group(1), 16)
    host_cycles = int(host.group(1), 16)
    print("  %-20s %12s" % ("implementation", "cycles"))
    print("  " + "-" * 34)
    print("  %-20s %12d" % ("tpu-lite", accel_cycles))
    print("  %-20s %12d" % ("pipeline5 CPU", host_cycles))
    print("\n  accelerator speedup: %.2fx" % (host_cycles / accel_cycles))
    print("  Note: TPU timing is doorbell-to-done; operand upload is excluded.")


def projected_us(cycles, mhz):
    return cycles / float(mhz)


def sized_image(program, ram_bytes):
    build_dir = "build/ram%d" % ram_bytes
    target = "%s/%s.hex" % (build_dir, program)
    subprocess.run(
        ["make", "-s", "-C", os.path.join(ROOT, "sw", "baremetal"),
         "BUILD_DIR=" + build_dir, "RAM_BYTES=%d" % ram_bytes, target],
        check=True)
    return os.path.join(ROOT, "sw", "baremetal", target)


def bench_tang(workdir):
    """Compare the exact independently-maximized profiles for both Tang boards."""
    boards = [
        ("Tang Nano 20K", 27, "tangnano20k.json",
         "tangnano20k-gpu.json", "tangnano20k-tpu.json"),
        ("Tang Primer 25K", 25, "tangprimer25k-ax2.json",
         "tangprimer25k-gpu.json", "tangprimer25k-tpu.json"),
    ]
    rows = {}
    images = {
        "cpu": sized_image("cpu_perf", 32768),
        "gpu": sized_image("gpu_perf", 16384),
        "tpu": sized_image("tpu", 32768),
    }
    print("\n== Tang board-max profiles: complete checked boundaries ==\n")
    for board, mhz, cpu_cfg, gpu_cfg, tpu_cfg in boards:
        cpu = run(make_board_sim_profile(workdir, cpu_cfg),
                  images["cpu"], 2000000)
        gpu = run(make_board_sim_profile(workdir, gpu_cfg),
                  images["gpu"], 3000000)
        tpu = run(make_board_sim_profile(workdir, tpu_cfg),
                  images["tpu"], 1000000)

        cpu_m = re.search(
            r"cpu_perf measured: cycles=(\d+) checksum=0x([0-9a-f]+)", cpu)
        gpu_matches = re.findall(
            r"(saxpy|poly)\s+N=\s*(\d+)\s+gpu=\s*(\d+).*?\n"
            r"\s+e2e upload=(\d+) compute=(\d+) "
            r"readback\+verify=(\d+) total=(\d+) checksum=0x([0-9a-f]+)",
            gpu)
        tpu_m = re.search(
            r"tpu e2e: upload=(\d+) compute=(\d+) "
            r"readback\+verify=(\d+) total=(\d+) checksum=0x([0-9a-f]+)",
            tpu)
        gpu_256 = {m[0]: m for m in gpu_matches if m[1] == "256"}
        if (not cpu_m or set(gpu_256) != {"saxpy", "poly"} or not tpu_m or
                "cpu_perf: PASS" not in cpu or "gpu-perf: PASS" not in gpu or
                "role tpu-lite: PASS" not in tpu):
            print("  %-20s FAILED TO RUN" % board)
            continue
        rows[board] = {
            "mhz": mhz,
            "cpu": (int(cpu_m.group(1)), cpu_m.group(2)),
            "gpu": {
                name: {
                    "upload": int(m[3]), "compute": int(m[4]),
                    "readback": int(m[5]), "total": int(m[6]),
                    "checksum": m[7],
                } for name, m in gpu_256.items()
            },
            "tpu": {
                "upload": int(tpu_m.group(1)),
                "compute": int(tpu_m.group(2)),
                "readback": int(tpu_m.group(3)),
                "total": int(tpu_m.group(4)),
                "checksum": tpu_m.group(5),
            },
        }

    print("  CPU (five workload windows; setup/UART excluded)")
    print("  %-20s %5s %12s %12s %12s" % (
        "board / max profile", "MHz", "cycles", "time (us)", "checksum"))
    for board, _, _, _, _ in boards:
        if board not in rows:
            continue
        cycles, checksum = rows[board]["cpu"]
        mhz = rows[board]["mhz"]
        print("  %-20s %5d %12d %12.1f   0x%s" % (
            board, mhz, cycles, projected_us(cycles, mhz), checksum))

    for kernel in ("saxpy", "poly"):
        print("\n  GPU %s, N=256 (complete offload)" % kernel)
        print("  %-20s %8s %8s %10s %8s %10s %12s" % (
            "board / max profile", "upload", "compute", "read+check",
            "total", "time (us)", "checksum"))
        for board, _, _, _, _ in boards:
            if board not in rows:
                continue
            data = rows[board]["gpu"][kernel]
            print("  %-20s %8d %8d %10d %8d %10.1f   0x%s" % (
                board, data["upload"], data["compute"], data["readback"],
                data["total"],
                projected_us(data["total"], rows[board]["mhz"]),
                data["checksum"]))

    print("\n  TPU 12x8x8 int8 GEMM (complete offload)")
    print("  %-20s %8s %8s %10s %8s %10s %12s" % (
        "board / max profile", "upload", "compute", "read+check",
        "total", "time (us)", "checksum"))
    for board, _, _, _, _ in boards:
        if board not in rows:
            continue
        data = rows[board]["tpu"]
        print("  %-20s %8d %8d %10d %8d %10.1f   0x%s" % (
            board, data["upload"], data["compute"], data["readback"],
            data["total"], projected_us(data["total"], rows[board]["mhz"]),
            data["checksum"]))

    nano, primer = "Tang Nano 20K", "Tang Primer 25K"
    if nano in rows and primer in rows:
        def time_ratio(group, item=None):
            n = rows[nano][group]
            p = rows[primer][group]
            if item:
                n, p = n[item], p[item]
            ncycles = n[0] if group == "cpu" else n["total"]
            pcycles = p[0] if group == "cpu" else p["total"]
            return (ncycles / rows[nano]["mhz"]) / (
                pcycles / rows[primer]["mhz"])

        print("\n  Primer wall-time speedup at target clocks:")
        print("    CPU %.2fx, GPU saxpy %.2fx, GPU poly %.2fx, TPU %.2fx" % (
            time_ratio("cpu"), time_ratio("gpu", "saxpy"),
            time_ratio("gpu", "poly"), time_ratio("tpu")))
        print("  Times are RTL-cycle projections; achieved P&R clocks are final.")


# Memory-system sweep for the render workload.  Cache geometry is (lines,
# words-per-line); the default 16x4 = 256 bytes is the composition smoke size.
RENDER_CFGS = [
    # (core, muldiv, cache component, lines, words/line, label)
    ("core.ax2", "muldiv.iterative32", "cache.direct-mapped", 16,   4,
     "baseline: 256 B WT $, div/32"),
    ("core.ax2", "muldiv.iterative32", "cache.direct-mapped", 1024, 4,
     "16 KiB WT $, div/32"),
    ("core.ax2", "muldiv.radix4",      "cache.direct-mapped", 1024, 4,
     "16 KiB WT $, div/16"),
    ("core.ax2", "muldiv.radix4",      "cache.writeback",     1024, 4,
     "16 KiB WB $, div/16"),
    ("core.ax2", "muldiv.radix4",      "cache.writeback",     1024, 8,
     "32 KiB WB $, div/16, 8w line"),
    ("core.minimal", "muldiv.radix4",    "cache.writeback",     1024, 8,
     "core.minimal, same memory"),
]


def bench_render(workdir):
    print("\n== Render workload: cycles per pixel (lower is better) ==")
    print("   delayed external memory, 32 MB, caches on\n")
    rows = []
    for core, md, cache, lines, wpl, note in RENDER_CFGS:
        base = json.load(open(os.path.join(CONFIGS, "sim-delayed.json")))
        base["name"] = "bench-render"
        base["components"]["core"] = core
        base["components"]["muldiv"] = md
        base["components"]["cache"] = cache
        base["settings"]["cache_lines"] = lines
        base["settings"]["cache_words_per_line"] = wpl
        path = os.path.join(workdir, "render-%s-%s-%s-%d-%d.json" % (
            core.replace(".", "-"), md.replace(".", "-"),
            cache.replace(".", "-"), lines, wpl))
        json.dump(base, open(path, "w"), indent=2)
        text = run(path, "render_perf.hex", 60000000)
        vals = dict(re.findall(r"(\w+): cycles=\d+ units=\d+ cyc_per_unit_x100=(\d+)", text))
        total = re.search(r"total cycles=(\d+)", text)
        if not vals or not total:
            print("  %-32s FAILED TO RUN" % note)
            continue
        rows.append((note, vals, int(total.group(1))))

    print("  %-30s %8s %8s %9s %8s %11s %9s" % (
        "configuration", "column", "span", "fixdiv", "blit", "total cyc", "speedup"))
    print("  " + "-" * 92)
    baseline = rows[0][2] if rows else 1
    for note, vals, total in rows:
        print("  %-30s %8s %8s %9s %8s %11d %8.2fx" % (
            note, fmt(vals.get("column")), fmt(vals.get("span")),
            fmt(vals.get("fixdiv")), fmt(vals.get("blit")),
            total, baseline / total))
    print("\n  column/span/blit are cycles per pixel; fixdiv is cycles per divide.")


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    choices = {"cpu", "gpu", "tpu", "tang", "render", "all"}
    if what not in choices:
        sys.exit("usage: tools/bench.py [cpu|gpu|tpu|tang|render|all]")
    needed = {
        "cpu": ("cpu_perf.hex",),
        "gpu": ("gpu.hex", "gpu1.hex"),
        "tpu": ("tpu.hex",),
        "tang": (),
        "render": ("render_perf.hex",),
        "all": ("cpu_perf.hex", "gpu.hex", "gpu1.hex", "gpu_perf.hex",
                "tpu.hex", "render_perf.hex"),
    }
    if any(not os.path.exists(os.path.join(IMAGES, image)) for image in needed[what]):
        sys.exit("missing images: run `make -C sw/baremetal images` first")
    with tempfile.TemporaryDirectory(prefix="axbench-") as workdir:
        if what in ("cpu", "all"):
            bench_cpu(workdir)
        if what in ("gpu", "all"):
            bench_gpu(workdir)
        if what in ("tpu", "all"):
            bench_tpu()
        if what in ("tang", "all"):
            bench_tang(workdir)
        if what in ("render", "all"):
            bench_render(workdir)
    print()


if __name__ == "__main__":
    main()
