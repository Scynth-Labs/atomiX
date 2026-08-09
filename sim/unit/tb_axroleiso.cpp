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
  top.role_reject_event = 0;
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

  top.role_reject_event = 1;
  top.watchdog_event = 1;
  top.role_irq_in = 1;
  tick(&top);
  top.role_reject_event = 0;
  top.watchdog_event = 0;
  top.role_irq_in = 0;
  top.eval();
  write_reg(&top, kLiveCommand, kLiveActivate);
  write_reg(&top, kLiveCommand, kLiveSnapshot);
  check(read_reg(&top, kLiveSequence) == 1, "snapshot command advances sequence");
  check(read_reg64(&top, kLiveCycles) != 0, "shell cycle counter advances");
  check(read_reg64(&top, kLiveWork) == 1, "completion rising edge is counted");
  check(read_reg64(&top, kLiveRejections) == 1,
        "explicit descriptor rejection is counted");
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
