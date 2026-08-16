// aX SoC v1 simulation/synthesis top level.  The core's Harvard ports are
// independently decoded onto dual-port BRAM/peripheral implementations.
module soc_top #(
  parameter logic [31:0] RESET_PC = 32'h0000_1000,
  parameter int unsigned RAM_BYTES = 128 * 1024,
  parameter int unsigned USE_DRAM_MODEL = 0,
  parameter int unsigned USE_SDRAM = 0,
  parameter int unsigned USE_CACHES = 0,
  // Cache geometry.  The default (16 lines x 4 words = 256 bytes) is a
  // composition smoke size, not a working one: any program with a real working
  // set needs kilobytes here.  Profiles set it through the `cache_lines` and
  // `cache_words_per_line` settings.
  parameter int unsigned CACHE_LINES = 16,
  parameter int unsigned CACHE_WORDS_PER_LINE = 4,
  parameter int unsigned SYNC_READ = 0,
  parameter ROM_INIT_FILE = "",
  parameter RAM_INIT_FILE = ""
) (
  input  logic       clk,
  input  logic       rst,
  input  logic       irq_external,
  output logic       uart_tx_valid,
  output logic [7:0] uart_tx_data,
  input  logic       uart_tx_ready,
  input  logic       uart_rx_valid,
  input  logic [7:0] uart_rx_data,
  output logic       uart_rx_ready,
  output logic       spi_sclk,
  output logic       spi_mosi,
  output logic       spi_cs_n,
  input  logic       spi_miso,
  output logic       sdram_cke,
  output logic       sdram_cs_n,
  output logic       sdram_ras_n,
  output logic       sdram_cas_n,
  output logic       sdram_we_n,
  output logic [1:0] sdram_ba,
  output logic [12:0] sdram_a,
  output logic [1:0] sdram_dqm,
  // verilator lint_off UNUSED
  input  logic [15:0] sdram_dq_i,
  // verilator lint_on UNUSED
  output logic [15:0] sdram_dq_o,
  output logic       sdram_dq_oe,
  output logic       sdram_init_done,
  output logic       finished,
  output logic [15:0] exit_code,
  // High while the CPU is parked in WFI with nothing pending.  Purely an
  // observation -- nothing inside the shell reads it -- but it lets a board
  // gate a clock and lets a simulator stop paying for cycles in which, by
  // construction, nothing can happen until an input changes.
  output logic       cpu_idle
);
  // One source of truth for the boot ROM window. Both bus muxes decode it, the
  // ROM instance is based at it, and the reset-into-ROM test that selects the
  // ROM's read timing compares against it.
  localparam logic [31:0] ROM_BASE = 32'h0000_1000;

  logic ibus_valid, ibus_ready, ibus_err;
  logic [31:0] ibus_addr, ibus_rdata, ibus_wdata;
  logic [3:0] ibus_wstrb;
  logic dbus_valid, dbus_ready, dbus_err;
  logic [31:0] dbus_addr, dbus_wdata, dbus_rdata;
  logic [3:0] dbus_wstrb;

  // Cache-facing aXbus signals keep the core interface unchanged.  With
  // caches disabled these are straight wires; with them enabled the caches
  // forward misses and all MMIO requests to the existing muxes below.
  logic i_bus_valid, i_bus_ready, i_bus_err;
  logic [31:0] i_bus_addr, i_bus_rdata, i_bus_wdata;
  logic [3:0] i_bus_wstrb;
  logic d_bus_valid, d_bus_ready, d_bus_err;
  logic [31:0] d_bus_addr, d_bus_rdata, d_bus_wdata;
  logic [3:0] d_bus_wstrb;

  logic i_rom_valid, i_ram_valid, i_test_valid, i_clint_valid, i_uart_valid, i_spi_valid;
  logic i_plic_valid, i_plic_ready, i_plic_err;
  logic [31:0] i_plic_rdata;
  logic d_rom_valid, d_ram_valid, d_test_valid, d_clint_valid, d_uart_valid, d_spi_valid;
  logic d_plic_valid, d_plic_ready, d_plic_err;
  logic [31:0] d_plic_rdata;
  // ---- Shell interrupt map ---------------------------------------------------
  // The single declaration of which device is which interrupt, and which core
  // input each target drives.  tools/gen_irq_map.py parses these localparams
  // and generates the C header the bare-metal runtime and aXos both include, so
  // a device added here reaches every software tree without a second list to
  // keep in step.  It also checks the ids cover their range exactly, which is
  // what catches adding a source and forgetting to bump the count.
  //
  // Sources are numbered from 1: the PLIC spec reserves id 0 for "no
  // interrupt".  Contexts follow the QEMU-virt single-hart layout.
  localparam int unsigned PLIC_SRC_UART = 1;  // receive holding register full
  localparam int unsigned PLIC_SRC_ROLE = 2;  // held while role STATUS.DONE
  localparam int unsigned PLIC_SOURCES  = 2;

  localparam int unsigned PLIC_CTX_M    = 0;  // hart 0 machine mode
  localparam int unsigned PLIC_CTX_S    = 1;  // hart 0 supervisor mode
  localparam int unsigned PLIC_CONTEXTS = 2;

  logic [PLIC_CONTEXTS-1:0] plic_irq;
  logic uart_irq_rx, role_irq, role_irq_raw, role_rst;
`ifdef AX_LIVE_ROLE_EVENTS
  logic role_reject;
`endif
  logic i_role_valid, d_role_valid;
  // Bus side of the role window terminates on the isolation fence, not on the
  // role: `*_role_*` below is the fenced bus view, `*_rolei_*` the role's own.
  logic i_shell_valid, d_shell_valid;
  logic i_shell_ready, i_shell_err, d_shell_ready, d_shell_err;
  logic [31:0] i_shell_rdata, d_shell_rdata;
  logic i_rolei_valid, d_rolei_valid;
  logic i_rolei_ready, i_rolei_err, d_rolei_ready, d_rolei_err;
  logic [31:0] i_rolei_rdata, d_rolei_rdata;
  logic i_rom_ready, i_rom_err, i_ram_ready, i_ram_err, i_test_ready, i_test_err;
  logic i_clint_ready, i_clint_err, i_uart_ready, i_uart_err;
  logic i_spi_ready, i_spi_err, i_role_ready, i_role_err;
  logic d_rom_ready, d_rom_err, d_ram_ready, d_ram_err, d_test_ready, d_test_err;
  logic d_clint_ready, d_clint_err, d_uart_ready, d_uart_err;
  logic d_spi_ready, d_spi_err, d_role_ready, d_role_err;
  logic [31:0] i_rom_rdata, i_ram_rdata, i_test_rdata, i_clint_rdata, i_uart_rdata;
  logic [31:0] i_spi_rdata, i_role_rdata;
  logic [31:0] d_rom_rdata, d_ram_rdata, d_test_rdata, d_clint_rdata, d_uart_rdata;
  logic [31:0] d_spi_rdata, d_role_rdata;
  logic irq_software, irq_timer;
  logic core_trace_valid, core_trace_trap;
  logic [31:0] core_trace_insn;

  // This is deliberately a lean CPU plug-in boundary.  A component supplies
  // the execution bus, interrupt inputs, and only the three commit signals
  // needed for cache maintenance.  RVFI and richer tracing stay optional
  // implementation features rather than requirements of the stock SoC.
  // verilator lint_off PINMISSING
  axcore #(.RESET_PC(RESET_PC)) u_core (
    .clk(clk), .rst(rst),
    .ibus_valid(ibus_valid), .ibus_addr(ibus_addr), .ibus_wdata(ibus_wdata),
    .ibus_wstrb(ibus_wstrb), .ibus_ready(ibus_ready),
    .ibus_rdata(ibus_rdata), .ibus_err(ibus_err),
    .dbus_valid(dbus_valid), .dbus_addr(dbus_addr), .dbus_wdata(dbus_wdata),
    .dbus_wstrb(dbus_wstrb), .dbus_ready(dbus_ready), .dbus_rdata(dbus_rdata),
    .dbus_err(dbus_err),
    .irq_software(irq_software), .irq_timer(irq_timer),
    // The board/testbench line and the on-chip controller are both sources of
    // the same machine-external interrupt; the core sees their union.
    .irq_external(irq_external || plic_irq[PLIC_CTX_M]),
    .irq_s_external(plic_irq[PLIC_CTX_S]), .cpu_idle(cpu_idle),
    .trace_valid(core_trace_valid),
    .trace_trap(core_trace_trap), .trace_insn(core_trace_insn)
  );
  // verilator lint_on PINMISSING

  // A fetch-side page-table walk can write a PTE that the data side cached,
  // so it invalidates the D$ after completing.  The I$ is deliberately not
  // invalidated on every ordinary data store: RISC-V makes FENCE.I the
  // explicit synchronization point for self-modifying code.  These are
  // registered pulses, avoiding a cross-port combinational ready loop.
  // verilator lint_off UNUSED
  logic i_write_complete, fence_i_complete;
  // verilator lint_on UNUSED
  always_ff @(posedge clk) begin
    if (rst) begin
      i_write_complete <= 1'b0;
      fence_i_complete <= 1'b0;
    end else begin
      i_write_complete <= ibus_valid && ibus_ready && |ibus_wstrb;
      fence_i_complete <= core_trace_valid && !core_trace_trap &&
                          (core_trace_insn & 32'h0000_707f) == 32'h0000_100f;
    end
  end

  // A write-back data cache can hold data that memory does not have yet, so an
  // instruction fetch must not reach memory while that cache is draining -- it
  // could refill from a word the data cache is still holding dirty.  Gating the
  // instruction port's `valid` keeps the instruction cache in its idle state
  // entirely, so it neither issues a fetch nor latches a stale response; when
  // the drain finishes it re-looks-up and sees correct memory.  Write-through
  // caches tie `flush_busy` low, so this costs them nothing.
  wire dcache_draining;
  wire ibus_valid_gated = ibus_valid && !dcache_draining;
  // Both are driven in either arm of the cache generate below.
  wire icache_c_ready;
  // The instruction cache's own drain is never gated on: nothing else fetches
  // from it, and a write-through instruction cache ties this low anyway.
  /* verilator lint_off UNUSED */
  wire icache_draining;
  /* verilator lint_on UNUSED */
  assign ibus_ready = (USE_CACHES != 0) ? (icache_c_ready && !dcache_draining)
                                        : i_bus_ready;
  // Tied off by the write-through caches and unused when caches are disabled.
  wire unused_cache_sigs = &{1'b0, icache_c_ready, dcache_draining};

  generate
    if (USE_CACHES != 0) begin : g_caches
      axcache #(.CACHE_BYTES(RAM_BYTES), .LINES(CACHE_LINES),
                .WORDS_PER_LINE(CACHE_WORDS_PER_LINE)) u_icache (
        .clk(clk), .rst(rst), .flush(fence_i_complete), .flush_busy(icache_draining),
        .c_valid(ibus_valid_gated), .c_addr(ibus_addr), .c_wdata(ibus_wdata), .c_wstrb(ibus_wstrb),
        .c_ready(icache_c_ready), .c_rdata(ibus_rdata), .c_err(ibus_err),
        .m_valid(i_bus_valid), .m_addr(i_bus_addr), .m_wdata(i_bus_wdata), .m_wstrb(i_bus_wstrb),
        .m_ready(i_bus_ready), .m_rdata(i_bus_rdata), .m_err(i_bus_err)
      );
      axcache #(.CACHE_BYTES(RAM_BYTES), .LINES(CACHE_LINES),
                .WORDS_PER_LINE(CACHE_WORDS_PER_LINE)) u_dcache (
        .clk(clk), .rst(rst), .flush(i_write_complete || fence_i_complete),
        .flush_busy(dcache_draining),
        .c_valid(dbus_valid), .c_addr(dbus_addr), .c_wdata(dbus_wdata), .c_wstrb(dbus_wstrb),
        .c_ready(dbus_ready), .c_rdata(dbus_rdata), .c_err(dbus_err),
        .m_valid(d_bus_valid), .m_addr(d_bus_addr), .m_wdata(d_bus_wdata), .m_wstrb(d_bus_wstrb),
        .m_ready(d_bus_ready), .m_rdata(d_bus_rdata), .m_err(d_bus_err)
      );
    end else begin : g_no_caches
      // No cache, so nothing can be dirty and the fetch gate never closes.
      assign dcache_draining = 1'b0;
      assign icache_c_ready  = i_bus_ready;
      assign i_bus_valid = ibus_valid_gated;
      assign i_bus_addr = ibus_addr;
      assign i_bus_wdata = ibus_wdata;
      assign i_bus_wstrb = ibus_wstrb;
      assign ibus_rdata = i_bus_rdata;
      assign ibus_err = i_bus_err;
      assign d_bus_valid = dbus_valid;
      assign d_bus_addr = dbus_addr;
      assign d_bus_wdata = dbus_wdata;
      assign d_bus_wstrb = dbus_wstrb;
      assign dbus_ready = d_bus_ready;
      assign dbus_rdata = d_bus_rdata;
      assign dbus_err = d_bus_err;
    end
  endgenerate

  axbus_mux #(.ROM_BASE(ROM_BASE), .RAM_SIZE(RAM_BYTES)) u_ibus_mux (
    .m_valid(i_bus_valid), .m_addr(i_bus_addr), .m_ready(i_bus_ready),
    .m_rdata(i_bus_rdata), .m_err(i_bus_err),
    .rom_valid(i_rom_valid), .rom_ready(i_rom_ready), .rom_rdata(i_rom_rdata), .rom_err(i_rom_err),
    .ram_valid(i_ram_valid), .ram_ready(i_ram_ready), .ram_rdata(i_ram_rdata), .ram_err(i_ram_err),
    .test_valid(i_test_valid), .test_ready(i_test_ready), .test_rdata(i_test_rdata), .test_err(i_test_err),
    .clint_valid(i_clint_valid), .clint_ready(i_clint_ready), .clint_rdata(i_clint_rdata), .clint_err(i_clint_err),
    .plic_valid(i_plic_valid), .plic_ready(i_plic_ready), .plic_rdata(i_plic_rdata), .plic_err(i_plic_err),
    .uart_valid(i_uart_valid), .uart_ready(i_uart_ready), .uart_rdata(i_uart_rdata), .uart_err(i_uart_err),
    .spi_valid(i_spi_valid), .spi_ready(i_spi_ready), .spi_rdata(i_spi_rdata), .spi_err(i_spi_err),
    .shell_valid(i_shell_valid), .shell_ready(i_shell_ready), .shell_rdata(i_shell_rdata), .shell_err(i_shell_err),
    .role_valid(i_role_valid), .role_ready(i_role_ready), .role_rdata(i_role_rdata), .role_err(i_role_err)
  );

  axbus_mux #(.ROM_BASE(ROM_BASE), .RAM_SIZE(RAM_BYTES)) u_dbus_mux (
    .m_valid(d_bus_valid), .m_addr(d_bus_addr), .m_ready(d_bus_ready),
    .m_rdata(d_bus_rdata), .m_err(d_bus_err),
    .rom_valid(d_rom_valid), .rom_ready(d_rom_ready), .rom_rdata(d_rom_rdata), .rom_err(d_rom_err),
    .ram_valid(d_ram_valid), .ram_ready(d_ram_ready), .ram_rdata(d_ram_rdata), .ram_err(d_ram_err),
    .test_valid(d_test_valid), .test_ready(d_test_ready), .test_rdata(d_test_rdata), .test_err(d_test_err),
    .clint_valid(d_clint_valid), .clint_ready(d_clint_ready), .clint_rdata(d_clint_rdata), .clint_err(d_clint_err),
    .plic_valid(d_plic_valid), .plic_ready(d_plic_ready), .plic_rdata(d_plic_rdata), .plic_err(d_plic_err),
    .uart_valid(d_uart_valid), .uart_ready(d_uart_ready), .uart_rdata(d_uart_rdata), .uart_err(d_uart_err),
    .spi_valid(d_spi_valid), .spi_ready(d_spi_ready), .spi_rdata(d_spi_rdata), .spi_err(d_spi_err),
    .shell_valid(d_shell_valid), .shell_ready(d_shell_ready), .shell_rdata(d_shell_rdata), .shell_err(d_shell_err),
    .role_valid(d_role_valid), .role_ready(d_role_ready), .role_rdata(d_role_rdata), .role_err(d_role_err)
  );

  // The boot ROM takes the board's registered-read timing only when the machine
  // actually resets into it. That is the whole population of profiles that
  // execute from ROM: the loader jumps to RAM and never returns, so a profile
  // resetting at RAM carries a baked payload and never fetches a ROM word.
  //
  // The distinction is not cosmetic. With no INIT_FILE the asynchronous form
  // optimises away completely, while the registered form leaves a handshake
  // behind and re-rolls packing -- measured at -252 LUT4 on `cpu` but +427 on
  // `morph-1pe`, which was enough to push both `role.morph` and `role.tpu-lite`
  // off a legal placement at 78-87% utilisation. Scoping it here means a baked
  // profile is bit-identical to one built before the ROM gained SYNC_READ,
  // while every loader profile still gets its 4 KiB ROM in BSRAM.
  localparam int unsigned ROM_SYNC_READ = (RESET_PC == ROM_BASE) ? SYNC_READ : 0;
  axrom #(.BASE(ROM_BASE), .SYNC_READ(ROM_SYNC_READ), .INIT_FILE(ROM_INIT_FILE)) u_rom (
    .clk(clk), .rst(rst), .i_valid(i_rom_valid), .i_addr(i_bus_addr), .i_wdata(i_bus_wdata),
    .i_wstrb(i_bus_wstrb), .i_ready(i_rom_ready), .i_rdata(i_rom_rdata), .i_err(i_rom_err),
    .d_valid(d_rom_valid), .d_addr(d_bus_addr), .d_wdata(d_bus_wdata),
    .d_wstrb(d_bus_wstrb), .d_ready(d_rom_ready), .d_rdata(d_rom_rdata), .d_err(d_rom_err)
  );

  // `axmem` is a replaceable component boundary. The reference implementation
  // preserves the Phase-6 BRAM/delayed/SDRAM selection; a DIY memory component
  // only needs to implement this dual-aXbus/pin-level boundary.
  axmem #(
    .RAM_BYTES(RAM_BYTES), .USE_DRAM_MODEL(USE_DRAM_MODEL),
    .USE_SDRAM(USE_SDRAM), .SYNC_READ(SYNC_READ), .RAM_INIT_FILE(RAM_INIT_FILE)
  ) u_ram (
    .clk(clk), .rst(rst), .i_valid(i_ram_valid), .i_addr(i_bus_addr),
    .i_wdata(i_bus_wdata), .i_wstrb(i_bus_wstrb), .i_ready(i_ram_ready),
    .i_rdata(i_ram_rdata), .i_err(i_ram_err), .d_valid(d_ram_valid),
    .d_addr(d_bus_addr), .d_wdata(d_bus_wdata), .d_wstrb(d_bus_wstrb),
    .d_ready(d_ram_ready), .d_rdata(d_ram_rdata), .d_err(d_ram_err),
    .sdram_cke(sdram_cke), .sdram_cs_n(sdram_cs_n),
    .sdram_ras_n(sdram_ras_n), .sdram_cas_n(sdram_cas_n),
    .sdram_we_n(sdram_we_n), .sdram_ba(sdram_ba), .sdram_a(sdram_a),
    .sdram_dqm(sdram_dqm), .sdram_dq_i(sdram_dq_i),
    .sdram_dq_o(sdram_dq_o), .sdram_dq_oe(sdram_dq_oe),
    .sdram_init_done(sdram_init_done)
  );

  test_finisher u_test (
    .clk(clk), .rst(rst), .i_valid(i_test_valid), .i_wdata(i_bus_wdata), .i_wstrb(i_bus_wstrb),
    .i_ready(i_test_ready), .i_rdata(i_test_rdata), .i_err(i_test_err),
    .d_valid(d_test_valid), .d_wdata(d_bus_wdata), .d_wstrb(d_bus_wstrb),
    .d_ready(d_test_ready), .d_rdata(d_test_rdata), .d_err(d_test_err),
    .finished(finished), .exit_code(exit_code)
  );

  clint u_clint (
    .clk(clk), .rst(rst), .i_valid(i_clint_valid), .i_addr(i_bus_addr), .i_wdata(i_bus_wdata),
    .i_wstrb(i_bus_wstrb), .i_ready(i_clint_ready), .i_rdata(i_clint_rdata), .i_err(i_clint_err),
    .d_valid(d_clint_valid), .d_addr(d_bus_addr), .d_wdata(d_bus_wdata), .d_wstrb(d_bus_wstrb),
    .d_ready(d_clint_ready), .d_rdata(d_clint_rdata), .d_err(d_clint_err),
    .irq_software(irq_software), .irq_timer(irq_timer)
  );

  uart u_uart (
    .clk(clk), .rst(rst), .i_valid(i_uart_valid), .i_addr(i_bus_addr), .i_wdata(i_bus_wdata),
    .i_wstrb(i_bus_wstrb), .i_ready(i_uart_ready), .i_rdata(i_uart_rdata), .i_err(i_uart_err),
    .d_valid(d_uart_valid), .d_addr(d_bus_addr), .d_wdata(d_bus_wdata), .d_wstrb(d_bus_wstrb),
    .d_ready(d_uart_ready), .d_rdata(d_uart_rdata), .d_err(d_uart_err),
    .tx_valid(uart_tx_valid), .tx_data(uart_tx_data), .tx_ready(uart_tx_ready),
    .rx_valid(uart_rx_valid), .rx_data(uart_rx_data), .rx_ready(uart_rx_ready),
    .irq_rx(uart_irq_rx)
  );

  // The role window's decoupling boundary.  Everything the bus sees of the
  // role passes through here, so the shell can fence the window off before the
  // fabric behind it is rewritten and no master can stall on a region that is
  // mid-reconfiguration.  Out of reset it forwards transparently, so a profile
  // that never writes its control register behaves exactly as it did before.
  axroleiso u_roleiso (
    .clk(clk), .rst(rst),
    .i_valid(i_shell_valid), .i_addr(i_bus_addr), .i_wdata(i_bus_wdata),
    .i_wstrb(i_bus_wstrb), .i_ready(i_shell_ready), .i_rdata(i_shell_rdata),
    .i_err(i_shell_err),
    .d_valid(d_shell_valid), .d_addr(d_bus_addr), .d_wdata(d_bus_wdata),
    .d_wstrb(d_bus_wstrb), .d_ready(d_shell_ready), .d_rdata(d_shell_rdata),
    .d_err(d_shell_err),
    .bus_i_valid(i_role_valid), .bus_i_ready(i_role_ready),
    .bus_i_rdata(i_role_rdata), .bus_i_err(i_role_err),
    .bus_d_valid(d_role_valid), .bus_d_ready(d_role_ready),
    .bus_d_rdata(d_role_rdata), .bus_d_err(d_role_err),
    .role_i_valid(i_rolei_valid), .role_i_ready(i_rolei_ready),
    .role_i_rdata(i_rolei_rdata), .role_i_err(i_rolei_err),
    .role_d_valid(d_rolei_valid), .role_d_ready(d_rolei_ready),
    .role_d_rdata(d_rolei_rdata), .role_d_err(d_rolei_err),
    .role_rst(role_rst),
    .role_irq_in(role_irq_raw), .role_irq_out(role_irq),
    // The role's own rejection line, qualified inside the fence.  It was tied
    // off here while no role produced one, which made DESCRIPTOR_REJECTIONS
    // read zero by construction; role.morph now drives it and the roles with
    // nothing to refuse tie it low at their own boundary, which is a statement
    // about those roles rather than about the shell.  A profile that declines
    // the producers compiles the port away entirely rather than tying it off,
    // because tying it off still perturbs the netlist -- see axroleiso.sv.
    // `watchdog_event` stays tied off because the fence derives the watchdog
    // itself from the role window, and this port is the extension point for a
    // future shell-level producer.
`ifdef AX_LIVE_ROLE_EVENTS
    .role_reject_event(role_reject),
`else
    .role_reject_event(1'b0),
`endif
    .watchdog_event(1'b0)
  );

  // The selected role component fills the fixed 0x4000_0000 window.  The
  // shell is identical whichever role (or role.none) a profile selects; a
  // role only sees its window and never replaces shell devices.
  axrole u_role (
    .clk(clk), .rst(role_rst), .i_valid(i_rolei_valid), .i_addr(i_bus_addr), .i_wdata(i_bus_wdata),
    .i_wstrb(i_bus_wstrb), .i_ready(i_rolei_ready), .i_rdata(i_rolei_rdata), .i_err(i_rolei_err),
    .d_valid(d_rolei_valid), .d_addr(d_bus_addr), .d_wdata(d_bus_wdata), .d_wstrb(d_bus_wstrb),
    .d_ready(d_rolei_ready), .d_rdata(d_rolei_rdata), .d_err(d_rolei_err),
    .irq(role_irq_raw)
`ifdef AX_LIVE_ROLE_EVENTS
    , .reject_event(role_reject)
`endif
  );

  // Device interrupts converge here, indexed by source id rather than
  // concatenated: a concatenation would encode the numbering a second time, in
  // bit order, where reordering it silently remaps every device.  This vector
  // is declared [SOURCES:1] so an index reads as the source id it is, and its
  // LSB still lands on the PLIC's `sources[0]`.  role.none ties its line low,
  // so a profile with no accelerator still presents a well-defined source.
  logic [PLIC_SOURCES:1] plic_sources;
  assign plic_sources[PLIC_SRC_UART] = uart_irq_rx;
  assign plic_sources[PLIC_SRC_ROLE] = role_irq;

  // Context 0 drives the core's machine external interrupt and context 1 its
  // supervisor external interrupt.  A bare-metal program owns context 0; aXos
  // runs in S-mode and owns context 1, which is what lets it claim and complete
  // without an M-mode round trip.
  plic #(.SOURCES(PLIC_SOURCES), .CONTEXTS(PLIC_CONTEXTS)) u_plic (
    .clk(clk), .rst(rst),
    .i_valid(i_plic_valid), .i_addr(i_bus_addr), .i_wdata(i_bus_wdata),
    .i_wstrb(i_bus_wstrb), .i_ready(i_plic_ready), .i_rdata(i_plic_rdata),
    .i_err(i_plic_err),
    .d_valid(d_plic_valid), .d_addr(d_bus_addr), .d_wdata(d_bus_wdata),
    .d_wstrb(d_bus_wstrb), .d_ready(d_plic_ready), .d_rdata(d_plic_rdata),
    .d_err(d_plic_err),
    .sources(plic_sources),
    .irq_external(plic_irq)
  );

  axspi u_spi (
    .clk(clk), .rst(rst), .i_valid(i_spi_valid), .i_addr(i_bus_addr), .i_wdata(i_bus_wdata),
    .i_wstrb(i_bus_wstrb), .i_ready(i_spi_ready), .i_rdata(i_spi_rdata), .i_err(i_spi_err),
    .d_valid(d_spi_valid), .d_addr(d_bus_addr), .d_wdata(d_bus_wdata), .d_wstrb(d_bus_wstrb),
    .d_ready(d_spi_ready), .d_rdata(d_spi_rdata), .d_err(d_spi_err),
    .spi_sclk(spi_sclk), .spi_mosi(spi_mosi), .spi_cs_n(spi_cs_n), .spi_miso(spi_miso)
  );
endmodule
