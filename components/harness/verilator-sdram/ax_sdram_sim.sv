// SDRAM behavioural model for the physical-controller integration test.
// It checks command/data ordering, DQM masks, and CAS=2 timing; it is not an
// electrical timing model.
module ax_sdram_sim (
  input logic clk,
  input logic rst,
  input logic cke,
  input logic cs_n,
  input logic ras_n,
  input logic cas_n,
  input logic we_n,
  input logic [1:0] ba,
  input logic [12:0] a,
  input logic [1:0] dqm,
  inout wire [15:0] dq,
  // Command activity at the pins, for observation only: nothing in the model
  // or the SoC reads these.  They exist because a test that merely *names* an
  // SDRAM target cannot tell a physical-pin run from a build that quietly
  // resolved to on-chip RAM -- `check-sdboot` did exactly that for months.  A
  // run whose counters are all zero never drove these pins, whatever the
  // target was called.
  output logic [31:0] cmd_activate,
  output logic [31:0] cmd_read,
  output logic [31:0] cmd_write,
  output logic [31:0] cmd_precharge,
  output logic [31:0] cmd_refresh
);
  logic [15:0] data_mem [0:16777215];
  logic [12:0] open_row [0:3];
  logic [3:0] open_valid;
  logic read_valid_0, read_valid_1;
  logic [15:0] read_data_0, read_data_1;
  wire [23:0] mem_index = {ba, open_row[ba], a[8:0]};

  assign dq = read_valid_1 ? read_data_1 : 16'hzzzz;

  // Decode the command from the control pins alone, without the open-row
  // qualification the data path applies: the question these answer is what the
  // controller *drove*, not what this model chose to honour.
  wire selected = cke && !cs_n;
  wire is_activate  = selected && !ras_n &&  cas_n &&  we_n;
  wire is_read      = selected &&  ras_n && !cas_n &&  we_n;
  wire is_write     = selected &&  ras_n && !cas_n && !we_n;
  wire is_precharge = selected && !ras_n &&  cas_n && !we_n;
  wire is_refresh   = selected && !ras_n && !cas_n &&  we_n;

  always_ff @(negedge clk) begin
    if (rst) begin
      cmd_activate  <= '0;
      cmd_read      <= '0;
      cmd_write     <= '0;
      cmd_precharge <= '0;
      cmd_refresh   <= '0;
    end else begin
      if (is_activate)  cmd_activate  <= cmd_activate + 1;
      if (is_read)      cmd_read      <= cmd_read + 1;
      if (is_write)     cmd_write     <= cmd_write + 1;
      if (is_precharge) cmd_precharge <= cmd_precharge + 1;
      if (is_refresh)   cmd_refresh   <= cmd_refresh + 1;
    end
  end

  always_ff @(negedge clk) begin
    if (rst) begin
      open_row[0] <= '0;
      open_row[1] <= '0;
      open_row[2] <= '0;
      open_row[3] <= '0;
      open_valid <= '0;
      read_valid_0 <= 1'b0;
      read_valid_1 <= 1'b0;
      read_data_0 <= '0;
      read_data_1 <= '0;
    end else begin
      read_valid_1 <= read_valid_0;
      read_data_1 <= read_data_0;
      read_valid_0 <= 1'b0;
      if (cke && !cs_n) begin
        if (!ras_n && cas_n && we_n) begin
          open_row[ba] <= a;
          open_valid[ba] <= 1'b1;
        end else if (ras_n && !cas_n && we_n && open_valid[ba]) begin
          read_valid_0 <= 1'b1;
          read_data_0 <= data_mem[mem_index];
        end else if (ras_n && !cas_n && !we_n && open_valid[ba]) begin
          if (!dqm[0]) data_mem[mem_index][7:0] <= dq[7:0];
          if (!dqm[1]) data_mem[mem_index][15:8] <= dq[15:8];
        end else if (!ras_n && cas_n && !we_n) begin
          if (a[10]) open_valid <= '0;
          else open_valid[ba] <= 1'b0;
        end
      end
    end
  end
endmodule
