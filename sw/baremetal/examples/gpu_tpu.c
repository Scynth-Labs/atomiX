/* Integration proof for role.gpu-tpu.  Both hard engines execute a real job
 * in one resident RTL model, with a guarded personality switch between them.
 * The FPGA loader profile remains payload-agnostic; this program is delivered
 * at runtime and is not part of the composite bitstream's identity. */
#include "platform.h"
#include "role.h"

static void fail(unsigned code, const char *what) {
  uart_puts("role gpu-tpu: FAIL ");
  uart_puts(what);
  uart_puts("\n");
  test_finish(code);
}

static uint32_t pack4(uint8_t a, uint8_t b, uint8_t c, uint8_t d) {
  return (uint32_t)a | ((uint32_t)b << 8) |
         ((uint32_t)c << 16) | ((uint32_t)d << 24);
}

static void clear_done(void) {
  mmio_write32(AX_ROLE_STATUS, AX_ROLE_STATUS_DONE);
  if (mmio_read32(AX_ROLE_STATUS) & AX_ROLE_STATUS_DONE)
    fail(2, "DONE did not clear");
}

static void run_gpu(void) {
  const uint32_t program[] = {
    gpu_insn(AX_GPU_TID, 0, 0, 0, 0),
    gpu_insn(AX_GPU_ADDI, 1, 0, 0, 5),
    gpu_insn(AX_GPU_STX, 0, 0, 1, 0),
    gpu_insn(AX_GPU_HALT, 0, 0, 0, 0),
  };
  for (unsigned i = 0; i < sizeof program / sizeof program[0]; ++i)
    mmio_write32(AX_ROLE_GPU_PROG + 4u * i, program[i]);
  mmio_write32(AX_ROLE_GPU_NTHREADS, 4u);
  mmio_write32(AX_ROLE_GPU_NINSN, 4u);
  role_ring_doorbell();
  role_wait_done();
  for (unsigned i = 0; i < 4; ++i)
    if (mmio_read32(AX_ROLE_GPU_DATA + 4u * i) != i + 5u)
      fail(3, "GPU kernel mismatch");
  clear_done();
}

static void run_tpu(void) {
  for (unsigned row = 0; row < 8; ++row) {
    uint8_t lo0 = 0, lo1 = 0, lo2 = 0, lo3 = 0;
    uint8_t hi0 = 0, hi1 = 0, hi2 = 0, hi3 = 0;
    if (row == 0) lo0 = 1;
    if (row == 1) lo1 = 1;
    if (row == 2) lo2 = 1;
    if (row == 3) lo3 = 1;
    if (row == 4) hi0 = 1;
    if (row == 5) hi1 = 1;
    if (row == 6) hi2 = 1;
    if (row == 7) hi3 = 1;
    mmio_write32(AX_ROLE_TPU_W + 8u * row, pack4(lo0, lo1, lo2, lo3));
    mmio_write32(AX_ROLE_TPU_W + 8u * row + 4u,
                 pack4(hi0, hi1, hi2, hi3));
  }
  mmio_write32(AX_ROLE_TPU_A, pack4(1, 2, 3, 4));
  mmio_write32(AX_ROLE_TPU_A + 4u, pack4(5, 6, 7, 8));
  mmio_write32(AX_ROLE_TPU_CTRL, 0);
  mmio_write32(AX_ROLE_TPU_M, 1);
  role_ring_doorbell();
  role_wait_done();
  for (unsigned col = 0; col < 8; ++col)
    if (mmio_read32(AX_ROLE_TPU_C + 4u * col) != col + 1u)
      fail(4, "TPU GEMM mismatch");
  clear_done();
}

int main(void) {
  if (mmio_read32(AX_ROLE_GPU_TPU_META_ID) != AX_ROLE_GPU_TPU_ID)
    fail(1, "composite discovery mismatch");
  if (mmio_read32(AX_ROLE_GPU_TPU_CAPS) !=
      (AX_ROLE_GPU_TPU_CAP_GPU | AX_ROLE_GPU_TPU_CAP_TPU))
    fail(1, "composite capabilities mismatch");
  if (role_id() != AX_ROLE_GPU_ID) fail(1, "GPU not selected after reset");

  run_gpu();
  mmio_write32(AX_ROLE_GPU_TPU_SELECT, AX_ROLE_GPU_TPU_TPU);
  if (role_id() != AX_ROLE_TPU_ID) fail(1, "TPU switch failed");
  run_tpu();
  mmio_write32(AX_ROLE_GPU_TPU_SELECT, AX_ROLE_GPU_TPU_GPU);
  if (role_id() != AX_ROLE_GPU_ID) fail(1, "GPU return switch failed");
  if (mmio_read32(AX_ROLE_GPU_COUNT) != 1u)
    fail(5, "GPU state was not retained");

  uart_puts("role gpu-tpu: PASS (GPU kernel, TPU GEMM, retained state)\n");
  test_finish(0);
}
