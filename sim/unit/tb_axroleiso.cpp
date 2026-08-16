// Directed test for the role-window isolation fence (docs/partial-reconfig.md).
//
// The property that matters is not "isolation makes reads return zero" -- it is
// that isolation contains a role which has stopped responding.  A fence that
// only works against a well-behaved role is worthless, because a well-behaved
// role never needed fencing.  So the central case here (`stuck role`) holds
// `ready` low forever, exactly as half-configured fabric would, and requires
// the bus to complete anyway once the fence is up.
#include <cstdint>
#include <cstdio>

#include "Vaxroleiso.h"
#include "verilated.h"

static constexpr uint32_t kBase = 0x10020000u;
static constexpr uint32_t kShellId = kBase + 0x0;
static constexpr uint32_t kIsoCtrl = kBase + 0x4;
static constexpr uint32_t kIsoStatus = kBase + 0x8;
static constexpr uint32_t kLiveId = kBase + 0x100;
static constexpr uint32_t kLiveVersion = kBase + 0x104;
static constexpr uint32_t kLiveCommand = kBase + 0x108;
static constexpr uint32_t kLiveSequence = kBase + 0x10c;
static constexpr uint32_t kLiveCycles = kBase + 0x110;
static constexpr uint32_t kLiveWork = kBase + 0x118;
static constexpr uint32_t kLiveStalls = kBase + 0x120;
static constexpr uint32_t kLiveRejections = kBase + 0x128;
static constexpr uint32_t kLiveWatchdogs = kBase + 0x130;
static constexpr uint32_t kLiveGeneration = kBase + 0x138;

static constexpr uint32_t kMagic = 0x61585348u;  // "aXSH"
static constexpr uint32_t kIsolate = 1u << 0;
static constexpr uint32_t kRoleReset = 1u << 1;
static constexpr uint32_t kLiveMagic = 0x61584c56u;  // "aXLV"
static constexpr uint32_t kLiveVersion10 = 0x00010000u;
static constexpr uint32_t kLiveSnapshot = 1u;
static constexpr uint32_t kLiveActivate = 2u;

// Must match -GWATCHDOG_CYCLES in the Makefile rule. The RTL default is much
// larger; this build pins a small threshold so a test can sit either side of
// the episode boundary without simulating thousands of idle cycles.
static constexpr int kWatchdogCycles = 16;

// TB_ROLE_EVENTS tracks the RTL's AX_LIVE_ROLE_EVENTS: the Makefile defines
// both together or neither.  A profile too tight to pay for the two producers
// compiles them out -- the role ABI's reject_event port included -- so this
// bench cannot even reference the port in that build, which is why the switch
// is compile-time rather than a flag.  Everything the fence does apart from
// those two counters must be identical either way, and the same stimulus runs
// in both builds to prove it.
#ifdef TB_ROLE_EVENTS
static constexpr bool role_events = true;
#else
static constexpr bool role_events = false;
#endif

// Expected counter value: the delta only lands when the producers are built.
static uint64_t with_events(uint64_t base, uint64_t delta) {
  return role_events ? base + delta : base;
}

static int failures = 0;

static void check(bool condition, const char* description) {
  if (!condition) {
    std::fprintf(stderr, "FAIL: %s\n", description);
    failures++;
  }
}

static void tick(Vaxroleiso* top) {
  top->clk = 0;
  top->eval();
  top->clk = 1;
  top->eval();
  top->clk = 0;
  top->eval();
}

// The role model. `ready` is a knob so a test can make the role stop answering
// without the fence being able to tell the difference from broken fabric.
static void set_role(Vaxroleiso* top, bool ready, uint32_t rdata, bool err) {
  top->role_i_ready = ready;
  top->role_i_rdata = rdata;
  top->role_i_err = err;
  top->role_d_ready = ready;
  top->role_d_rdata = rdata;
  top->role_d_err = err;
}

static void write_reg(Vaxroleiso* top, uint32_t address, uint32_t value) {
  top->d_addr = address;
  top->d_wdata = value;
  top->d_wstrb = 0xf;
  top->d_valid = 1;
  top->eval();
  check(top->d_ready && !top->d_err, "ISO_CTRL write completes without error");
  tick(top);
  top->d_valid = 0;
  top->d_wstrb = 0;
  top->eval();
}

static void write_ctrl(Vaxroleiso* top, uint32_t value) {
  write_reg(top, kIsoCtrl, value);
}

static uint32_t read_reg(Vaxroleiso* top, uint32_t address) {
  top->d_addr = address;
  top->d_wstrb = 0;
  top->d_valid = 1;
  top->eval();
  check(top->d_ready && !top->d_err, "control read completes without error");
  uint32_t value = top->d_rdata;
  top->d_valid = 0;
  top->eval();
  return value;
}

static uint64_t read_reg64(Vaxroleiso* top, uint32_t address) {
  const uint64_t low = read_reg(top, address);
  return low | (static_cast<uint64_t>(read_reg(top, address + 4)) << 32);
}

int main(int argc, char** argv) {
  Verilated::commandArgs(argc, argv);
  Vaxroleiso top;

  top.clk = 0;
  top.rst = 1;
  top.i_valid = 0;
  top.i_addr = 0;
  top.i_wdata = 0;
  top.i_wstrb = 0;
  top.d_valid = 0;
  top.d_addr = 0;
  top.d_wdata = 0;
  top.d_wstrb = 0;
  top.bus_i_valid = 0;
  top.bus_d_valid = 0;
  top.role_irq_in = 0;
#ifdef TB_ROLE_EVENTS
  top.role_reject_event = 0;
#endif
  top.watchdog_event = 0;
  set_role(&top, true, 0xdeadbeefu, false);
  tick(&top);
  top.rst = 0;
  tick(&top);

  check(read_reg(&top, kShellId) == kMagic, "SHELL_ID reads \"aXSH\"");
  check(read_reg(&top, kIsoCtrl) == 0, "ISO_CTRL resets clear");
  check(read_reg(&top, kIsoStatus) == 0, "role is not isolated out of reset");
  check(top.role_rst == 0, "role reset is released out of reset");

  // Live FPGA telemetry is in the same immutable shell page, not in the role.
  check(read_reg(&top, kLiveId) == kLiveMagic, "LIVE_ID reads \"aXLV\"");
  check(read_reg(&top, kLiveVersion) == kLiveVersion10,
        "LIVE_VERSION reports schema 1.0");
  check(read_reg(&top, kLiveSequence) == 0, "no telemetry snapshot after reset");

#ifdef TB_ROLE_EVENTS
  top.role_reject_event = 1;
#endif
  top.watchdog_event = 1;
  top.role_irq_in = 1;
  tick(&top);
#ifdef TB_ROLE_EVENTS
  top.role_reject_event = 0;
#endif
  top.watchdog_event = 0;
  top.role_irq_in = 0;
  top.eval();
  write_reg(&top, kLiveCommand, kLiveActivate);
  write_reg(&top, kLiveCommand, kLiveSnapshot);
  check(read_reg(&top, kLiveSequence) == 1, "snapshot command advances sequence");
  check(read_reg64(&top, kLiveCycles) != 0, "shell cycle counter advances");
  check(read_reg64(&top, kLiveWork) == 1, "completion rising edge is counted");
  check(read_reg64(&top, kLiveRejections) == with_events(0, 1),
        "explicit descriptor rejection is counted");
  // The watchdog *port* is not gated: it is the extension point for a
  // shell-level producer, and a profile that declines the fence's own
  // derivation has not declined someone else's.
  check(read_reg64(&top, kLiveWatchdogs) == 1,
        "explicit watchdog event is counted");
  check(read_reg64(&top, kLiveGeneration) == 1,
        "verified activation command advances generation");

  // The fence owns the observation point, so it can count a role-window stall
  // even when the changing role never responds.
  set_role(&top, false, 0, false);
  top.bus_d_valid = 1;
  tick(&top);
  top.bus_d_valid = 0;
  set_role(&top, true, 0xdeadbeefu, false);
  write_reg(&top, kLiveCommand, kLiveSnapshot);
  check(read_reg64(&top, kLiveStalls) == 1,
        "one waiting role-window cycle is one memory stall");

  // An unknown command is an error and must not manufacture a generation.
  top.d_addr = kLiveCommand;
  top.d_wdata = 99;
  top.d_wstrb = 0xf;
  top.d_valid = 1;
  top.eval();
  check(top.d_ready && top.d_err, "unknown LIVE_COMMAND reports an access error");
  tick(&top);
  top.d_valid = 0;
  top.d_wstrb = 0;
  top.eval();
  write_reg(&top, kLiveCommand, kLiveSnapshot);
  check(read_reg64(&top, kLiveGeneration) == 1,
        "rejected live command does not advance generation");

  // Transparent by default: a profile that never touches this register must
  // see exactly the window it saw before the fence existed.
  top.bus_d_valid = 1;
  top.eval();
  check(top.role_d_valid == 1, "role sees the transaction when transparent");
  check(top.bus_d_ready == 1, "bus sees the role's ready when transparent");
  check(top.bus_d_rdata == 0xdeadbeefu, "bus sees the role's data when transparent");
  top.bus_d_valid = 0;
  top.eval();

  top.role_irq_in = 1;
  top.eval();
  check(top.role_irq_out == 1, "completion interrupt passes when transparent");

  // An error response is forwarded rather than swallowed: isolation is not an
  // excuse to hide a genuine role bus error.
  set_role(&top, true, 0, true);
  top.bus_d_valid = 1;
  top.eval();
  check(top.bus_d_err == 1, "role bus errors reach the bus when transparent");
  top.bus_d_valid = 0;
  top.eval();
  set_role(&top, true, 0xdeadbeefu, false);

  // ---- The case the fence exists for: a role that has stopped answering.
  // This is what a partially reconfigured region looks like from the bus.
  set_role(&top, false, 0, false);
  top.bus_d_valid = 1;
  top.eval();
  check(top.bus_d_ready == 0, "a stuck role stalls the bus while transparent");

  // Raising the fence must complete the access even though the role never
  // will. Waiting for the role to retire first would deadlock here, which is
  // precisely why ISOLATE is immediate and unconditional.
  top.bus_d_valid = 0;
  top.eval();
  write_ctrl(&top, kIsolate);

  check(read_reg(&top, kIsoStatus) == kIsolate, "ISO_STATUS reports the fence is up");

  top.bus_d_valid = 1;
  top.eval();
  check(top.bus_d_ready == 1, "an isolated stuck role no longer stalls the bus");
  check(top.bus_d_rdata == 0, "an isolated window reads as zero");
  check(top.bus_d_err == 0, "an isolated window does not raise a bus error");
  check(top.role_d_valid == 0, "an isolated role is offered no transaction");
  top.bus_d_valid = 0;
  top.eval();

  top.bus_i_valid = 1;
  top.eval();
  check(top.bus_i_ready == 1, "the fetch port is fenced too");
  check(top.role_i_valid == 0, "an isolated role sees no fetch either");
  top.bus_i_valid = 0;
  top.eval();

  // Reading zero is what makes the fence need no new software path: zero is
  // already ROLE_ID's "no role present" encoding, so discovery just works.
  check(top.bus_d_rdata == 0, "an isolated ROLE_ID reads as absent");

  // ---- What the counters may and may not count.
  //
  // Reading the fenced window is the documented way to rediscover a role after
  // a swap, so those reads must not be charged to DESCRIPTOR_REJECTIONS: a
  // nonzero rejection delta makes a trial ineligible for fitness, and a normal
  // swap would then disqualify itself.  The isolated accesses above already
  // ran, so the counter must still stand where it did.
  const uint64_t rejections_fenced = read_reg64(&top, kLiveRejections);
  top.bus_d_valid = 1;
  tick(&top);
  top.bus_d_valid = 0;
  top.eval();
  write_reg(&top, kLiveCommand, kLiveSnapshot);
  check(read_reg64(&top, kLiveRejections) == rejections_fenced,
        "reading the fenced window is not a descriptor rejection");

#ifdef TB_ROLE_EVENTS
  // A rejection pulse arriving while the role is fenced is not counted either:
  // a role mid-rewrite is not reporting anything the shell should believe,
  // which is the same rule the completion line already follows.
  top.role_reject_event = 1;
  tick(&top);
  top.role_reject_event = 0;
  top.eval();
  write_reg(&top, kLiveCommand, kLiveSnapshot);
  check(read_reg64(&top, kLiveRejections) == rejections_fenced,
        "a rejection reported by an isolated role is not counted");
#endif

  // Transparent again: the role's own line is what the counter is for.
  write_ctrl(&top, 0);
  set_role(&top, true, 0xdeadbeefu, false);
#ifdef TB_ROLE_EVENTS
  const uint64_t rejections_live = read_reg64(&top, kLiveRejections);
  top.role_reject_event = 1;
  tick(&top);
  top.role_reject_event = 0;
  top.eval();
  write_reg(&top, kLiveCommand, kLiveSnapshot);
  check(read_reg64(&top, kLiveRejections) == with_events(rejections_live, 1),
        "a descriptor the role refuses is counted once");

  // Edge-triggered, so fabric that comes up with the line stuck high costs one
  // event rather than one per cycle for as long as it stays wrong.
  const uint64_t rejections_stuck = read_reg64(&top, kLiveRejections);
  top.role_reject_event = 1;
  for (int cycle = 0; cycle < 8; cycle++) tick(&top);
  top.role_reject_event = 0;
  top.eval();
  write_reg(&top, kLiveCommand, kLiveSnapshot);
  check(read_reg64(&top, kLiveRejections) == with_events(rejections_stuck, 1),
        "a stuck rejection line is one event, not one per cycle");
#endif

  // Ordinary role traffic is not a rejection: the counter has to separate
  // "refused" from "served", or it just counts role traffic.
  const uint64_t rejections_transparent = read_reg64(&top, kLiveRejections);
  top.bus_d_valid = 1;
  tick(&top);
  top.bus_d_valid = 0;
  top.eval();
  write_reg(&top, kLiveCommand, kLiveSnapshot);
  check(read_reg64(&top, kLiveRejections) == rejections_transparent,
        "a served access is not counted as a rejection");

  // ---- The watchdog must be able to move on its own.
  //
  // It was once supplied only by the input port, which the shell tied to zero,
  // so the telemetry reported "no watchdog events" for every possible input.
  // A counter that cannot increment is not evidence, so the tests below drive
  // the *derived* source with the port held low: if anyone ties the derivation
  // off again, these fail rather than silently reading zero.
  check(top.watchdog_event == 0, "the derived watchdog runs with its port low");

  // A role that stops answering holds the transfer outstanding.
  // WATCHDOG_CYCLES is pinned small for this build so the episode boundary is
  // checkable; the mechanism does not depend on the threshold.
  const uint64_t watchdogs_before = read_reg64(&top, kLiveWatchdogs);
  set_role(&top, false, 0, false);
  top.bus_d_valid = 1;
  top.eval();
  for (int cycle = 0; cycle < kWatchdogCycles * 3; cycle++) tick(&top);
  top.bus_d_valid = 0;
  set_role(&top, true, 0xdeadbeefu, false);
  top.eval();
  write_reg(&top, kLiveCommand, kLiveSnapshot);
  check(read_reg64(&top, kLiveWatchdogs) == with_events(watchdogs_before, 1),
        "a role that stops answering raises exactly one watchdog event");

  // One hung job is one event however long it hangs -- otherwise the count is
  // just a slow copy of the stall counter and says nothing extra.
  const uint64_t watchdogs_after_first = read_reg64(&top, kLiveWatchdogs);
  set_role(&top, false, 0, false);
  top.bus_d_valid = 1;
  top.eval();
  for (int cycle = 0; cycle < kWatchdogCycles * 2; cycle++) tick(&top);
  top.bus_d_valid = 0;
  set_role(&top, true, 0xdeadbeefu, false);
  top.eval();
  write_reg(&top, kLiveCommand, kLiveSnapshot);
  check(read_reg64(&top, kLiveWatchdogs) == with_events(watchdogs_after_first, 1),
        "a second stall episode is a second event, not a per-cycle count");

  // A brief stall is not a watchdog: the shell must not report a role dead for
  // being slow, or the counter cannot be used to justify tearing one out.
  const uint64_t watchdogs_before_brief = read_reg64(&top, kLiveWatchdogs);
  set_role(&top, false, 0, false);
  top.bus_d_valid = 1;
  top.eval();
  for (int cycle = 0; cycle < kWatchdogCycles - 2; cycle++) tick(&top);
  top.bus_d_valid = 0;
  set_role(&top, true, 0xdeadbeefu, false);
  top.eval();
  write_reg(&top, kLiveCommand, kLiveSnapshot);
  check(read_reg64(&top, kLiveWatchdogs) == watchdogs_before_brief,
        "a stall shorter than the threshold raises no watchdog event");

  write_ctrl(&top, kIsolate);
  top.eval();

  // Fabric coming up in an unknown state must not storm the PLIC with a
  // level-sensitive line nothing will ever clear.
  top.role_irq_in = 1;
  top.eval();
  check(top.role_irq_out == 0, "an isolated role cannot assert the completion line");

  // ---- Role reset, so rewritten fabric starts from a defined state.
  write_ctrl(&top, kIsolate | kRoleReset);
  check(top.role_rst == 1, "ROLE_RESET holds the role in reset");
  check(read_reg(&top, kIsoCtrl) == (kIsolate | kRoleReset), "ISO_CTRL reads back");

  // ---- Dropping the fence restores the window exactly as it was.
  set_role(&top, true, 0x1234abcdu, false);
  write_ctrl(&top, 0);
  check(top.role_rst == 0, "releasing ROLE_RESET releases the role");
  check(read_reg(&top, kIsoStatus) == 0, "ISO_STATUS reports the fence is down");

  top.bus_d_valid = 1;
  top.eval();
  check(top.role_d_valid == 1, "the role is reachable again after de-isolation");
  check(top.bus_d_rdata == 0x1234abcdu, "the role's data reaches the bus again");
  top.bus_d_valid = 0;
  top.eval();

  top.role_irq_in = 1;
  top.eval();
  check(top.role_irq_out == 1, "the completion line works again after de-isolation");
  top.role_irq_in = 0;
  top.eval();

  // ---- Control-register decode, same contract as every other shell device.
  top.d_addr = kBase + 0x40;  // unmapped offset
  top.d_wstrb = 0;
  top.d_valid = 1;
  top.eval();
  check(top.d_ready && top.d_err, "unknown offset reports an access error");
  top.d_valid = 0;
  top.eval();

  top.d_addr = kIsoCtrl + 1;  // misaligned
  top.d_valid = 1;
  top.eval();
  check(top.d_ready && top.d_err, "misaligned access reports an access error");
  top.d_valid = 0;
  top.eval();

  // A write that errors must not reach the register: a misaligned store to
  // ISO_CTRL raising the fence would be a way to wedge the window by accident.
  top.d_addr = kIsoCtrl + 1;
  top.d_wdata = kIsolate;
  top.d_wstrb = 0xf;
  top.d_valid = 1;
  top.eval();
  tick(&top);
  top.d_valid = 0;
  top.d_wstrb = 0;
  top.eval();
  check(read_reg(&top, kIsoStatus) == 0, "an erroring write does not raise the fence");

  if (failures) {
    std::fprintf(stderr, "tb_axroleiso: %d FAILURE(S)\n", failures);
    return 1;
  }
  std::puts("tb_axroleiso: PASS");
  return 0;
}
