// Directed PLIC contract test: priority/threshold gating, the claim-complete
// handshake, and the level-sensitive gateway behaviour the shell's devices
// actually rely on (a source still asserted at complete becomes pending again
// rather than being lost).
#include <cstdint>
#include <cstdio>

#include "Vplic.h"
#include "verilated.h"

static constexpr uint32_t kBase = 0x0c000000u;
static constexpr uint32_t kPriority1 = kBase + 4;
static constexpr uint32_t kPriority2 = kBase + 8;
static constexpr uint32_t kPending = kBase + 0x1000;

// Context 0 is hart 0 M-mode and context 1 is hart 0 S-mode, at the QEMU-virt
// strides (0x80 between enable words, 0x1000 between context blocks).
static constexpr uint32_t kEnable = kBase + 0x2000;
static constexpr uint32_t kThreshold = kBase + 0x200000;
static constexpr uint32_t kClaim = kBase + 0x200004;
static constexpr uint32_t kEnableS = kBase + 0x2080;
static constexpr uint32_t kThresholdS = kBase + 0x201000;
static constexpr uint32_t kClaimS = kBase + 0x201004;

// irq_external is one bit per context.
static constexpr uint32_t kMirq = 1u << 0;
static constexpr uint32_t kSirq = 1u << 1;

// Source index within `sources`: bit 0 is source 1, bit 1 is source 2.  Named
// by id, not by device: which shell device drives which line is the shell's
// business, and a component test that encoded it would have to change whenever
// the shell's device list did.
static constexpr uint32_t kSource1 = 1u << 0;
static constexpr uint32_t kSource2 = 1u << 1;

static int failures = 0;

static void check(bool condition, const char* description) {
  if (!condition) {
    std::fprintf(stderr, "FAIL: %s\n", description);
    failures++;
  }
}

static void tick(Vplic* top) {
  top->clk = 0;
  top->eval();
  top->clk = 1;
  top->eval();
  top->clk = 0;
  top->eval();
}

static void write_d(Vplic* top, uint32_t address, uint32_t value) {
  top->d_addr = address;
  top->d_wdata = value;
  top->d_wstrb = 0xf;
  top->d_valid = 1;
  top->eval();
  check(top->d_ready && !top->d_err, "PLIC write completes without error");
  tick(top);
  top->d_valid = 0;
  top->d_wstrb = 0;
  top->eval();
}

// A CLAIM read is side-effecting, so it must be a real bus cycle: present the
// read, sample rdata, then clock the edge that takes the source into service.
static uint32_t read_d(Vplic* top, uint32_t address) {
  top->d_addr = address;
  top->d_wstrb = 0;
  top->d_valid = 1;
  top->eval();
  check(top->d_ready && !top->d_err, "PLIC read completes without error");
  const uint32_t result = top->d_rdata;
  tick(top);
  top->d_valid = 0;
  top->eval();
  return result;
}

// Read without clocking an edge, for registers where the observation itself
// must not disturb the claim state.
static uint32_t peek_d(Vplic* top, uint32_t address) {
  top->d_addr = address;
  top->d_wstrb = 0;
  top->d_valid = 0;
  top->eval();
  return top->d_rdata;
}

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Vplic top;
  top.clk = 0;
  top.rst = 1;
  top.i_valid = top.d_valid = 0;
  top.i_addr = top.d_addr = 0;
  top.i_wdata = top.d_wdata = 0;
  top.i_wstrb = top.d_wstrb = 0;
  top.sources = 0;
  tick(&top);
  top.rst = 0;
  top.eval();

  check(top.irq_external == 0, "reset leaves no context interrupted");

  // An asserted source with priority 0 and no enable must stay invisible:
  // "never deliver" is the reset posture, so an unprogrammed PLIC cannot
  // storm the core.
  top.sources = kSource1;
  top.eval();
  check(!(top.irq_external & kMirq), "priority 0 and disabled source does not deliver");
  check(peek_d(&top, kPending) & 0x2, "gateway marks an asserted source pending");

  write_d(&top, kEnable, 0x2);  // enable source 1 only
  top.eval();
  check(!(top.irq_external & kMirq), "enabled source with priority 0 still does not deliver");

  write_d(&top, kPriority1, 1);
  top.eval();
  check((top.irq_external & kMirq), "enabled source above threshold delivers");

  // Threshold is strictly-greater-than, so an equal priority is masked.
  write_d(&top, kThreshold, 1);
  top.eval();
  check(!(top.irq_external & kMirq), "threshold masks an equal priority");
  write_d(&top, kThreshold, 0);
  top.eval();
  check((top.irq_external & kMirq), "lowering the threshold restores delivery");

  // Bit 0 is reserved: source id 0 means "no interrupt" and must not be
  // enableable.
  write_d(&top, kEnable, 0xffffffffu);
  check(!(peek_d(&top, kEnable) & 1), "enable bit 0 is reserved and reads zero");

  // ---- Priority arbitration -------------------------------------------------
  // Assert both lines and give source 2 the higher priority;
  // it must win the claim regardless of id order.
  top.sources = kSource1 | kSource2;

  // Equal priorities must tie-break to the LOWEST id -- the rule the spec
  // requires, and what the downward-counting winner loop implements.
  write_d(&top, kPriority1, 2);
  write_d(&top, kPriority2, 2);
  top.eval();
  check((top.irq_external & kMirq), "either source delivers");
  check(read_d(&top, kClaim) == 1, "equal priorities tie-break to the lowest id");
  write_d(&top, kClaim, 1);  // complete, leaving nothing in service

  // With distinct priorities the higher one wins regardless of id order.
  write_d(&top, kPriority1, 1);
  write_d(&top, kPriority2, 3);
  top.eval();
  check(read_d(&top, kClaim) == 2, "higher priority source wins the claim");

  // In service: source 2 is masked, so the remaining eligible source is 1.
  check(!(peek_d(&top, kPending) & 0x4), "claimed source is no longer pending");
  check((top.irq_external & kMirq), "the other source still delivers while one is in service");
  check(read_d(&top, kClaim) == 1, "second claim returns the remaining source");
  check(!(top.irq_external & kMirq), "no interrupt while every source is in service");

  // ---- Level-sensitive complete ---------------------------------------------
  // Source 2 is still held high. Completing it must make it pending again --
  // this is the property a level-driven device (a role holding STATUS.DONE, a
  // UART holding a byte) depends on, and one an edge-latching gateway gets wrong.
  write_d(&top, kClaim, 2);
  top.eval();
  check((top.irq_external & kMirq), "a still-asserted source re-arms after complete");
  check(peek_d(&top, kPending) & 0x4, "re-armed source reads pending again");

  // Now drop the line first, as a handler that serviced the device would:
  // completing a deasserted source must leave it quiet.
  check(read_d(&top, kClaim) == 2, "re-armed source can be claimed again");
  top.sources = kSource1;  // the device serviced by the handler dropped its line
  top.eval();
  write_d(&top, kClaim, 2);
  top.eval();
  check(!(peek_d(&top, kPending) & 0x4), "a deasserted source stays quiet after complete");

  // Source 1 is still in service from its earlier claim and still asserted.
  check(!(top.irq_external & kMirq), "source in service does not deliver even while asserted");
  write_d(&top, kClaim, 1);
  top.eval();
  check((top.irq_external & kMirq), "completing the last source re-arms it");

  // A claim with nothing eligible returns 0 rather than a stale id.
  top.sources = 0;
  top.eval();
  check(!(top.irq_external & kMirq), "no asserted source means no interrupt");
  check(read_d(&top, kClaim) == 0, "claim with nothing pending returns zero");

  // Completing an id this instance does not implement is ignored, not faulted:
  // that is what a handler writing back a stale claim should get.
  write_d(&top, kClaim, 9);

  // ---- The S-mode context ---------------------------------------------------
  // Context 1 is what aXos claims from. Everything above programmed context 0
  // only, so the S context must still be inert: this is the check that would
  // catch a second context aliased onto the first one's registers.
  top.sources = kSource2;
  top.eval();
  check((top.irq_external & kMirq), "context 0 still delivers what it enabled");
  check(!(top.irq_external & kSirq), "an unprogrammed context does not deliver");
  check(peek_d(&top, kEnableS) == 0, "the S context has its own enable word");

  // Hand the source over: disable it in M, enable it in S. Only the S line
  // should move -- if the two contexts shared state, both would.
  write_d(&top, kEnable, 0);
  write_d(&top, kEnableS, 0x4);
  top.eval();
  check(!(top.irq_external & kMirq), "disabling in context 0 stops its delivery");
  check((top.irq_external & kSirq), "enabling in context 1 delivers there");

  // Thresholds are per context too. Priority 3 against a threshold of 3 fails
  // the strictly-greater rule, and context 0's threshold must not be touched.
  write_d(&top, kThresholdS, 3);
  top.eval();
  check(!(top.irq_external & kSirq), "the S context has its own threshold");
  check(peek_d(&top, kThreshold) == 0, "context 0's threshold is unchanged");
  write_d(&top, kThresholdS, 0);
  top.eval();
  check((top.irq_external & kSirq), "lowering the S threshold restores delivery");

  // Claim and complete from the S context, including the level-sensitive
  // re-arm -- the exact sequence the aXos handler runs.
  check(read_d(&top, kClaimS) == 2, "the S context claims its own winner");
  check(!(peek_d(&top, kPending) & 0x4), "a source claimed by S is no longer pending");
  check(!(top.irq_external & kSirq), "no S interrupt while its source is in service");
  write_d(&top, kClaimS, 2);
  top.eval();
  check((top.irq_external & kSirq), "a still-asserted source re-arms for S after complete");

  // A source in service is in service for everyone: if M claims it, S must not
  // also be handed the same interrupt.
  write_d(&top, kEnable, 0x4);  // both contexts now enable source 2
  top.eval();
  check((top.irq_external & kMirq) && (top.irq_external & kSirq),
        "a source enabled in both contexts delivers to both");
  check(read_d(&top, kClaim) == 2, "context 0 claims it first");
  check(!(top.irq_external & kSirq), "the other context no longer sees it pending");
  check(read_d(&top, kClaimS) == 0, "a claim by S returns zero once M holds it");
  write_d(&top, kClaim, 2);

  top.sources = 0;
  top.eval();
  check(top.irq_external == 0, "dropping every line quiets both contexts");

  // ---- Bus contract ---------------------------------------------------------
  top.d_addr = kBase + 0x24;  // unimplemented priority slot
  top.d_wstrb = 0;
  top.d_valid = 1;
  top.eval();
  check(top.d_ready && top.d_err, "unknown offset reports an access error");
  top.d_valid = 0;
  top.eval();

  top.d_addr = kThreshold + 1;  // misaligned
  top.d_valid = 1;
  top.eval();
  check(top.d_ready && top.d_err, "misaligned access reports an access error");
  top.d_valid = 0;
  top.eval();

  if (failures) {
    std::fprintf(stderr, "tb_plic: %d FAILURE(S)\n", failures);
    return 1;
  }
  std::puts("tb_plic: PASS");
  return 0;
}
