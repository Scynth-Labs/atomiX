// Physical 8-N-1 UART PHY for a board top. The SoC UART keeps its 16550-like
// MMIO contract; this module converts its byte-valid interface to serial pins.
module axuart_phy #(
  parameter integer CLOCK_HZ = 25_000_000,
  parameter integer BAUD = 115_200
) (
  input  logic clk,
  input  logic rst,
  input  logic tx_valid,
  input  logic [7:0] tx_data,
  output logic tx_ready,
  output logic tx,
  input  logic rx,
  output logic rx_valid,
  output logic [7:0] rx_data,
  input  logic rx_ready
);
  localparam integer CLKS_PER_BIT = (CLOCK_HZ + BAUD / 2) / BAUD;
  localparam integer HALF_CLKS = CLKS_PER_BIT / 2;
  localparam integer COUNT_WIDTH = $clog2(CLKS_PER_BIT + 1);
  // verilator lint_off WIDTH
  localparam [COUNT_WIDTH-1:0] BIT_LAST = CLKS_PER_BIT - 1;
  localparam [COUNT_WIDTH-1:0] START_LAST = HALF_CLKS - 1;
  // verilator lint_on WIDTH

  logic tx_busy;
  logic [COUNT_WIDTH-1:0] tx_count, rx_count;
  logic [3:0] tx_bit;
  logic [2:0] rx_bit;
  logic [9:0] tx_shift;
  logic [7:0] rx_shift;
  logic rx_meta, rx_sync;
  typedef enum logic [1:0] { RX_IDLE, RX_START, RX_DATA, RX_STOP } rx_state_t;
  rx_state_t rx_state;

  assign tx_ready = !tx_busy;
  assign tx = tx_busy ? tx_shift[0] : 1'b1;

  always_ff @(posedge clk) begin
    if (rst) begin
      tx_busy <= 1'b0;
      tx_count <= '0;
      tx_bit <= '0;
      tx_shift <= 10'h3ff;
    end else if (!tx_busy) begin
      if (tx_valid) begin
        tx_busy <= 1'b1;
        tx_count <= '0;
        tx_bit <= '0;
        tx_shift <= {1'b1, tx_data, 1'b0};
      end
    end else if (tx_count == BIT_LAST) begin
      tx_count <= '0;
      if (tx_bit == 4'd9) tx_busy <= 1'b0;
      else begin
        tx_bit <= tx_bit + 1'b1;
        tx_shift <= {1'b1, tx_shift[9:1]};
      end
    end else begin
      tx_count <= tx_count + 1'b1;
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      rx_meta <= 1'b1;
      rx_sync <= 1'b1;
      rx_count <= '0;
      rx_bit <= '0;
      rx_shift <= '0;
      rx_state <= RX_IDLE;
      rx_valid <= 1'b0;
      rx_data <= '0;
    end else begin
      rx_meta <= rx;
      rx_sync <= rx_meta;
      if (rx_valid && rx_ready) rx_valid <= 1'b0;

      case (rx_state)
        RX_IDLE: begin
          if (!rx_valid && !rx_sync) begin
            rx_count <= '0;
            rx_state <= RX_START;
          end
        end
        RX_START: begin
          if (rx_count == START_LAST) begin
            if (!rx_sync) begin
              rx_count <= '0;
              rx_bit <= '0;
              rx_state <= RX_DATA;
            end else begin
              rx_state <= RX_IDLE;
            end
          end else begin
            rx_count <= rx_count + 1'b1;
          end
        end
        RX_DATA: begin
          if (rx_count == BIT_LAST) begin
            rx_count <= '0;
            rx_shift[rx_bit] <= rx_sync;
            if (rx_bit == 3'd7) rx_state <= RX_STOP;
            else rx_bit <= rx_bit + 1'b1;
          end else begin
            rx_count <= rx_count + 1'b1;
          end
        end
        RX_STOP: begin
          if (rx_count == BIT_LAST) begin
            rx_count <= '0;
            rx_state <= RX_IDLE;
            if (rx_sync) begin
              rx_data <= rx_shift;
              rx_valid <= 1'b1;
            end
          end else begin
            rx_count <= rx_count + 1'b1;
          end
        end
        default: rx_state <= RX_IDLE;
      endcase
    end
  end
endmodule
