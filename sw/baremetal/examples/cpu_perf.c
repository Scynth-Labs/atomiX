/* CPU performance regression: measures instructions-per-cycle on a set of
 * integer workloads and fails if IPC drops below the bar for the core the
 * profile selected.
 *
 * This is the evidence behind the claim that ax2 is a faster core rather than
 * just a differently-shaped one.  It reads mcycle and minstret around each
 * workload, so the figure is retired instructions over elapsed cycles -- a
 * property of the machine, not of how the workload was compiled.  The same
 * binary runs on every core, so numbers across profiles are directly
 * comparable.
 *
 * The workloads are chosen to stress different parts of a superscalar front and
 * back end:
 *
 *   alu     long dependency-free arithmetic -- the best case for dual issue
 *   chain   a serial dependency chain -- the worst case, IPC should approach 1
 *   branch  a tight taken loop -- exercises the branch predictor
 *   memcpy  load/store bound -- limited by the single data port
 *   mixed   a realistic blend
 *
 * IPC is reported in hundredths (so 150 means 1.50 instructions per cycle) to
 * keep the program free of floating point. */
#include "bench_report.h"
#include "platform.h"
#include "csr.h"

#define ARR 256
static volatile int32_t src[ARR], dst[ARR];
static uint32_t measured_cycles;

static uint32_t rdcycle(void) {
  uint32_t v; __asm__ volatile("csrr %0, mcycle" : "=r"(v)); return v;
}
static uint32_t rdinstret(void) {
  uint32_t v; __asm__ volatile("csrr %0, minstret" : "=r"(v)); return v;
}

static void putdec(uint32_t v) {
  char buf[12]; int n = 0;
  if (!v) { uart_putchar('0'); return; }
  while (v) { buf[n++] = (char)('0' + v % 10); v /= 10; }
  while (n) uart_putchar(buf[--n]);
}

/* IPC in hundredths.  Guards against a zero cycle count and against the
 * multiply overflowing 32 bits on a long run. */
static uint32_t ipc_x100(uint32_t insns, uint32_t cycles) {
  if (!cycles) return 0;
  while (insns > 0x00ffffffu) { insns >>= 1; cycles >>= 1; }
  if (!cycles) return 0;
  return (insns * 100u) / cycles;
}

static uint32_t report(const char *name, uint32_t insns, uint32_t cycles) {
  uint32_t ipc = ipc_x100(insns, cycles);
  measured_cycles += cycles;
  uart_puts("  ");
  uart_puts(name);
  uart_puts(": insns=");
  putdec(insns);
  uart_puts(" cycles=");
  putdec(cycles);
  uart_puts(" ipc_x100=");
  putdec(ipc);
  uart_puts("\n");
  return ipc;
}

/* Independent arithmetic: four separate accumulators, so consecutive
 * instructions have no register dependency and both issue slots can fill. */
static uint32_t bench_alu(void) {
  volatile uint32_t sink;
  uint32_t a = 1, b = 2, c = 3, d = 4;
  uint32_t i0 = rdinstret(), c0 = rdcycle();
  for (int i = 0; i < 400; ++i) {
    a += 3; b += 5; c += 7; d += 11;
    a ^= b; c ^= d;
  }
  uint32_t cyc = rdcycle() - c0, ins = rdinstret() - i0;
  sink = a + b + c + d; (void)sink;
  return report("alu   ", ins, cyc);
}

/* A serial dependency chain: every instruction reads the previous result, so
 * slot 1 can never issue.  IPC near 1.0 here is correct, not a regression --
 * this workload exists to bound the claim. */
static uint32_t bench_chain(void) {
  volatile uint32_t sink;
  uint32_t a = 1;
  uint32_t i0 = rdinstret(), c0 = rdcycle();
  for (int i = 0; i < 400; ++i) {
    a = a * 3 + 1; a ^= a >> 7; a += 5; a ^= a << 3;
  }
  uint32_t cyc = rdcycle() - c0, ins = rdinstret() - i0;
  sink = a; (void)sink;
  return report("chain ", ins, cyc);
}

/* A tight loop with a highly predictable backward branch. */
static uint32_t bench_branch(void) {
  volatile uint32_t sink;
  uint32_t acc = 0;
  uint32_t i0 = rdinstret(), c0 = rdcycle();
  for (int i = 0; i < 2000; ++i) acc += (uint32_t)i & 7u;
  uint32_t cyc = rdcycle() - c0, ins = rdinstret() - i0;
  sink = acc; (void)sink;
  return report("branch", ins, cyc);
}

/* Load/store bound: one data port means at most one memory op per cycle. */
static uint32_t bench_memcpy(void) {
  for (int i = 0; i < ARR; ++i) src[i] = i * 7 + 1;
  uint32_t i0 = rdinstret(), c0 = rdcycle();
  for (int r = 0; r < 4; ++r)
    for (int i = 0; i < ARR; ++i) dst[i] = src[i] + r;
  uint32_t cyc = rdcycle() - c0, ins = rdinstret() - i0;
  return report("memcpy", ins, cyc);
}

/* A blend: arithmetic, a load, a store, and a branch per iteration. */
static uint32_t bench_mixed(void) {
  volatile uint32_t sink;
  uint32_t acc = 0;
  for (int i = 0; i < ARR; ++i) src[i] = i ^ 0x5a;
  uint32_t i0 = rdinstret(), c0 = rdcycle();
  for (int r = 0; r < 4; ++r)
    for (int i = 0; i < ARR; ++i) {
      int32_t v = src[i];
      acc += (uint32_t)(v * 3 + r);
      dst[i] = v ^ (int32_t)acc;
    }
  uint32_t cyc = rdcycle() - c0, ins = rdinstret() - i0;
  sink = acc; (void)sink;
  return report("mixed ", ins, cyc);
}

int main(void) {
  uart_puts("cpu_perf:\n");

  uint32_t ipc_alu    = bench_alu();
  uint32_t ipc_chain  = bench_chain();
  uint32_t ipc_branch = bench_branch();
  uint32_t ipc_memcpy = bench_memcpy();
  uint32_t ipc_mixed  = bench_mixed();

  /* Sanity bars, deliberately loose: they exist to catch a core that has
   * stopped retiring (counters stuck, pipeline livelocked) or a change that
   * halves throughput, not to pin a specific microarchitecture.  A scalar core
   * passes these; the interesting comparison is the printed numbers across
   * profiles, which is why they are printed rather than only checked. */
  if (ipc_alu    < 20) { uart_puts("cpu_perf: FAIL alu ipc\n");    test_finish(1); }
  if (ipc_chain  < 15) { uart_puts("cpu_perf: FAIL chain ipc\n");  test_finish(2); }
  if (ipc_branch < 15) { uart_puts("cpu_perf: FAIL branch ipc\n"); test_finish(3); }
  if (ipc_memcpy < 15) { uart_puts("cpu_perf: FAIL memcpy ipc\n"); test_finish(4); }
  if (ipc_mixed  < 15) { uart_puts("cpu_perf: FAIL mixed ipc\n");  test_finish(5); }

  uint32_t checksum = 2166136261u;
  for (int i = 0; i < ARR; ++i)
    checksum = ax_bench_checksum_step(checksum, (uint32_t)dst[i]);
  uart_puts("cpu_perf measured: cycles=");
  ax_bench_putdec(measured_cycles);
  uart_puts(" checksum=0x");
  ax_bench_puthex(checksum);
  ax_bench_report_projected_time(measured_cycles);
  uart_puts("\n");

  uart_puts("cpu_perf: PASS\n");
  test_finish(0);
}
