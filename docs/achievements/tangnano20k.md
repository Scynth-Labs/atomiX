# Tang Nano 20K achievements

## Hardware status

- **Available:** no; no Tang Nano 20K is currently owned or physically tested.
- **FPGA target:** Gowin GW2AR-18C.
- **Strongest evidence:** simulation plus synthesis/fit. Nothing on this page
  is physical-board evidence.

## Successfully completed without hardware

- The board component, constraints, and Gowin flow exist.
- The baseline CPU profile maps its 32 KiB main memory to 32 DPB block-RAM
  cells and passes the synchronous-read RTL simulation.
- The baseline profile fits at roughly 11k LUT4 and 2.7k flip-flops.
- The six-lane GPU profile synthesizes at roughly 20.2k LUT4 (97%, tight) and
  passes the shared GPU functional/performance simulations.
- The folded TPU profile synthesizes at 14,300 LUT4, 3,239 flip-flops, and
  24 `MULT9X9` cells and passes its functional simulation.

## Pending

- [ ] Acquire or borrow a Tang Nano 20K before claiming any BOARD result.
- [ ] Run place-and-route and retain timing/evidence for the exact current
  CPU, GPU, and TPU images.
- [ ] Verify USB/JTAG identity, volatile SRAM programming, UART pins and baud,
  reset behavior, and recovery after power cycle.
- [ ] Run the CPU hello, GPU result checks, and TPU GEMM/reference comparison on
  silicon.
- [ ] Reassess the six-lane GPU's 97% utilization on the physical routing flow;
  do not archive it as working until timing and UART tests pass.
- [ ] Add a dedicated bring-up guide when hardware becomes available.
