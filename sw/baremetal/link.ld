/* atomiX bare-metal link script: everything lives in RAM at 0x80000000.
 * The Makefile supplies __ram_bytes (128 KiB by default, 16/32 KiB for Tang
 * FPGA payloads), so the stack top and link-time overflow check match the
 * actual synthesized memory. vaddr == paddr; no ELF headers are mapped. */
OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY {
  RAM (rwx) : ORIGIN = 0x80000000, LENGTH = __ram_bytes
}

PHDRS {
  load PT_LOAD;
}

SECTIONS {
  .text : {
    *(.text.init)           /* reset entry first */
    *(.text*)
  } > RAM :load

  .rodata : { *(.rodata*) *(.srodata*) } > RAM :load
  .data   : { *(.data*) *(.sdata*) } > RAM :load

  /* riscv-tests-style HTIF exit word; kept in its own section so the
   * symbol is easy to find and align. */
  .tohost ALIGN(16) : { *(.tohost) } > RAM :load

  .bss (NOLOAD) : {
    __bss_start = .;
    *(.bss*) *(.sbss*) *(COMMON)
    __bss_end = .;
  } > RAM

  __stack_top = ORIGIN(RAM) + LENGTH(RAM);
  _end = .;

  /DISCARD/ : { *(.comment) *(.note*) *(.riscv.attributes) }
}
