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
static constexpr uint32_t kEnable = kBase + 0x2000;
static constexpr uint32_t kThreshold = kBase + 0x200000;
static constexpr uint32_t kClaim = kBase + 0x200004;

// Source index within `sources`: bit 0 is source 1, bit 1 is source 2.
static constexpr uint32_t kUartLine = 1u << 0;
static constexpr uint32_t kRoleLine = 1u << 1;

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

  check(!top.irq_external, "reset leaves no interrupt asserted");

  // An asserted source with priority 0 and no enable must stay invisible:
  // "never deliver" is the reset posture, so an unprogrammed PLIC cannot
  // storm the core.
  top.sources = kUartLine;
  top.eval();
  check(!top.irq_external, "priority 0 and disabled source does not deliver");
  check(peek_d(&top, kPending) & 0x2, "gateway marks an asserted source pending");

  write_d(&top, kEnable, 0x2);  // enable source 1 only
  top.eval();
  check(!top.irq_external, "enabled source with priority 0 still does not deliver");

  write_d(&top, kPriority1, 1);
  top.eval();
  check(top.irq_external, "enabled source above threshold delivers");

  // Threshold is strictly-greater-than, so an equal priority is masked.
  write_d(&top, kThreshold, 1);
  top.eval();
  check(!top.irq_external, "threshold masks an equal priority");
  write_d(&top, kThreshold, 0);
  top.eval();
  check(top.irq_external, "lowering the threshold restores delivery");

  // Bit 0 is reserved: source id 0 means "no interrupt" and must not be
  // enableable.
  write_d(&top, kEnable, 0xffffffffu);
  check(!(peek_d(&top, kEnable) & 1), "enable bit 0 is reserved and reads zero");

  // ---- Priority arbitration -------------------------------------------------
  // Assert both lines and give source 2 (role completion) the higher priority;
  // it must win the claim regardless of id order.
  top.sources = kUartLine | kRoleLine;

  // Equal priorities must tie-break to the LOWEST id -- the rule the spec
  // requires, and what the downward-counting winner loop implements.
  write_d(&top, kPriority1, 2);
  write_d(&top, kPriority2, 2);
  top.eval();
  check(top.irq_external, "either source delivers");
  check(read_d(&top, kClaim) == 1, "equal priorities tie-break to the lowest id");
  write_d(&top, kClaim, 1);  // complete, leaving nothing in service

  // With distinct priorities the higher one wins regardless of id order.
  write_d(&top, kPriority1, 1);
  write_d(&top, kPriority2, 3);
  top.eval();
  check(read_d(&top, kClaim) == 2, "higher priority source wins the claim");

  // In service: source 2 is masked, so the remaining eligible source is 1.
  check(!(peek_d(&top, kPending) & 0x4), "claimed source is no longer pending");
  check(top.irq_external, "the other source still delivers while one is in service");
  check(read_d(&top, kClaim) == 1, "second claim returns the remaining source");
  check(!top.irq_external, "no interrupt while every source is in service");

  // ---- Level-sensitive complete ---------------------------------------------
  // Source 2 is still held high. Completing it must make it pending again --
  // this is the property a level-driven device (role STATUS.DONE, UART RX)
  // depends on, and the one an edge-latching gateway would get wrong.
  write_d(&top, kClaim, 2);
  top.eval();
  check(top.irq_external, "a still-asserted source re-arms after complete");
  check(peek_d(&top, kPending) & 0x4, "re-armed source reads pending again");

  // Now drop the line first, as a handler that serviced the device would:
  // completing a deasserted source must leave it quiet.
  check(read_d(&top, kClaim) == 2, "re-armed source can be claimed again");
  top.sources = kUartLine;  // role cleared its STATUS.DONE
  top.eval();
  write_d(&top, kClaim, 2);
  top.eval();
  check(!(peek_d(&top, kPending) & 0x4), "a deasserted source stays quiet after complete");

  // Source 1 is still in service from its earlier claim and still asserted.
  check(!top.irq_external, "source in service does not deliver even while asserted");
  write_d(&top, kClaim, 1);
  top.eval();
  check(top.irq_external, "completing the last source re-arms it");

  // A claim with nothing eligible returns 0 rather than a stale id.
  top.sources = 0;
  top.eval();
  check(!top.irq_external, "no asserted source means no interrupt");
  check(read_d(&top, kClaim) == 0, "claim with nothing pending returns zero");

  // Completing an id this instance does not implement is ignored, not faulted:
  // that is what a handler writing back a stale claim should get.
  write_d(&top, kClaim, 9);

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
