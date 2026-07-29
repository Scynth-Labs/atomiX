OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY {
  ROM (rx) : ORIGIN = 0x00001000, LENGTH = 4K
  /* One KiB at the top of the selected RAM is loader-only scratch. Kernel
   * payloads are capped below the kernel's reserved final stack page. */
  SCRATCH (rw) : ORIGIN = 0x80000000 + AXBOOT_RAM_BYTES - 1K, LENGTH = 1K
}
SECTIONS {
  .text : { *(.text.init) *(.text*) } > ROM
  .rodata : { *(.rodata*) *(.srodata*) } > ROM
  .data : { *(.data*) *(.sdata*) } > ROM
  .bss (NOLOAD) : { *(.bss*) *(.sbss*) *(COMMON) } > SCRATCH
  __stack_top = ORIGIN(SCRATCH) + LENGTH(SCRATCH);
  /DISCARD/ : { *(.comment) *(.note*) *(.riscv.attributes) }
}
