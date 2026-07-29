/* Initial aXos image: execute from the shared 128 KiB RAM window.  Keeping
 * vaddr == paddr lets M-mode construct the first identity map before S-mode
 * begins. A higher-half layout can be introduced once process VM exists. */
OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY {
  RAM (rwx) : ORIGIN = 0x80000000, LENGTH = AXOS_RAM_BYTES
}

PHDRS {
  load PT_LOAD;
}

SECTIONS {
  .text : {
    *(.text.init)
    *(.text*)
  } > RAM :load

  .usertext ALIGN(4K) : { *(.usertext) } > RAM :load
  .rodata : { *(.rodata*) *(.srodata*) } > RAM :load
  .data   : { *(.data*) *(.sdata*) } > RAM :load

  /* Bootstrap tables precede small BSS objects so their 4 KiB alignment does
   * not strand almost a full page in compact-memory profiles. */
  .page_tables (NOLOAD) : {
    *(.page_tables*)
  } > RAM

  .bss (NOLOAD) : {
    __bss_start = .;
    *(.bss*) *(.sbss*) *(COMMON)
    __bss_end = .;
  } > RAM

  /* Storage builds stage a filesystem-backed ELF here. The file read fills
   * every consumed byte, so clearing this region during boot is wasted work,
   * especially through the physical SDRAM controller. */
  .noinit (NOLOAD) : {
    *(.noinit*)
  } > RAM

  /* Reserve one page for the initial M/S/trap stack. The allocator owns only
   * pages below this boundary, never the live bootstrap stack. */
  __stack_bottom = ORIGIN(RAM) + LENGTH(RAM) - 4K;
  /* supervisor_trap_entry subtracts one 128-byte frame before storing. Put
   * that frame at the bottom of the reserved page, not below its boundary. */
  __trap_stack_top = __stack_bottom + 128;
  __stack_top = ORIGIN(RAM) + LENGTH(RAM);
  _end = .;

  ASSERT(SIZEOF(.usertext) <= 4K,
         "user payload must fit one mapped 4 KiB page")
  ASSERT(_end <= __stack_bottom,
         "kernel image, BSS, and noinit overlap the reserved stack")

  /DISCARD/ : { *(.comment) *(.note*) *(.riscv.attributes) }
}
