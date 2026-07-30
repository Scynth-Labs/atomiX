// One aXbus master to the fixed v1 SoC address map.  Instantiate once for
// each aXcore master; each slave exposes matching independent I/D ports.
module axbus_mux #(
  parameter logic [31:0] ROM_BASE   = 32'h0000_1000,
  parameter logic [31:0] ROM_SIZE   = 32'h0000_1000,
  parameter logic [31:0] TEST_BASE  = 32'h0010_0000,
  parameter logic [31:0] TEST_SIZE  = 32'h0000_1000,
  parameter logic [31:0] CLINT_BASE = 32'h0200_0000,
  parameter logic [31:0] CLINT_SIZE = 32'h0001_0000,
  // The PLIC's claim/complete register lives at offset 0x20_0004, so its
  // window is larger than the other devices'.  Base and layout follow the
  // qemu-virt convention, as the CLINT's do.
  parameter logic [31:0] PLIC_BASE  = 32'h0c00_0000,
  parameter logic [31:0] PLIC_SIZE  = 32'h0040_0000,
  parameter logic [31:0] UART_BASE  = 32'h1000_0000,
  parameter logic [31:0] UART_SIZE  = 32'h0000_1000,
  parameter logic [31:0] SPI_BASE   = 32'h1001_0000,
  parameter logic [31:0] SPI_SIZE   = 32'h0000_1000,
  parameter logic [31:0] ROLE_BASE  = 32'h4000_0000,
  parameter logic [31:0] ROLE_SIZE  = 32'h0001_0000,
  parameter logic [31:0] RAM_BASE   = 32'h8000_0000,
  parameter logic [31:0] RAM_SIZE   = 32'h0002_0000
) (
  input  logic        m_valid,
  input  logic [31:0] m_addr,
  output logic        m_ready,
  output logic [31:0] m_rdata,
  output logic        m_err,

  output logic        rom_valid,
  input  logic        rom_ready,
  input  logic [31:0] rom_rdata,
  input  logic        rom_err,
  output logic        ram_valid,
  input  logic        ram_ready,
  input  logic [31:0] ram_rdata,
  input  logic        ram_err,
  output logic        test_valid,
  input  logic        test_ready,
  input  logic [31:0] test_rdata,
  input  logic        test_err,
  output logic        clint_valid,
  input  logic        clint_ready,
  input  logic [31:0] clint_rdata,
  input  logic        clint_err,
  output logic        plic_valid,
  input  logic        plic_ready,
  input  logic [31:0] plic_rdata,
  input  logic        plic_err,
  output logic        uart_valid,
  input  logic        uart_ready,
  input  logic [31:0] uart_rdata,
  input  logic        uart_err,
  output logic        spi_valid,
  input  logic        spi_ready,
  input  logic [31:0] spi_rdata,
  input  logic        spi_err,
  output logic        role_valid,
  input  logic        role_ready,
  input  logic [31:0] role_rdata,
  input  logic        role_err
);
  wire hit_rom   = m_addr >= ROM_BASE   && m_addr - ROM_BASE   < ROM_SIZE;
  wire hit_ram   = m_addr >= RAM_BASE   && m_addr - RAM_BASE   < RAM_SIZE;
  wire hit_test  = m_addr >= TEST_BASE  && m_addr - TEST_BASE  < TEST_SIZE;
  wire hit_clint = m_addr >= CLINT_BASE && m_addr - CLINT_BASE < CLINT_SIZE;
  wire hit_plic  = m_addr >= PLIC_BASE  && m_addr - PLIC_BASE  < PLIC_SIZE;
  wire hit_uart  = m_addr >= UART_BASE  && m_addr - UART_BASE  < UART_SIZE;
  wire hit_spi   = m_addr >= SPI_BASE   && m_addr - SPI_BASE   < SPI_SIZE;
  wire hit_role  = m_addr >= ROLE_BASE  && m_addr - ROLE_BASE  < ROLE_SIZE;

  always_comb begin
    rom_valid   = m_valid && hit_rom;
    ram_valid   = m_valid && hit_ram;
    test_valid  = m_valid && hit_test;
    clint_valid = m_valid && hit_clint;
    plic_valid  = m_valid && hit_plic;
    uart_valid  = m_valid && hit_uart;
    spi_valid   = m_valid && hit_spi;
    role_valid  = m_valid && hit_role;
    m_ready     = 1'b0;
    m_rdata     = 32'b0;
    m_err       = 1'b0;
    if (m_valid) begin
      if (hit_rom) begin
        m_ready = rom_ready; m_rdata = rom_rdata; m_err = rom_err;
      end else if (hit_ram) begin
        m_ready = ram_ready; m_rdata = ram_rdata; m_err = ram_err;
      end else if (hit_test) begin
        m_ready = test_ready; m_rdata = test_rdata; m_err = test_err;
      end else if (hit_clint) begin
        m_ready = clint_ready; m_rdata = clint_rdata; m_err = clint_err;
      end else if (hit_plic) begin
        m_ready = plic_ready; m_rdata = plic_rdata; m_err = plic_err;
      end else if (hit_uart) begin
        m_ready = uart_ready; m_rdata = uart_rdata; m_err = uart_err;
      end else if (hit_spi) begin
        m_ready = spi_ready; m_rdata = spi_rdata; m_err = spi_err;
      end else if (hit_role) begin
        m_ready = role_ready; m_rdata = role_rdata; m_err = role_err;
      end else begin
        // Decode misses complete with an error; masters must never hang.
        m_ready = 1'b1; m_err = 1'b1;
      end
    end
  end
endmodule
