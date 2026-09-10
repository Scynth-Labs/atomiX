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
    KEEP(*(.text.evolution))
    KEEP(*(.text.fitness))
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
    KEEP(*(.bss.evolution))
    *(.bss*) *(.sbss*) *(COMMON)
    __bss_end = .;
  } > RAM

  /* Storage builds stage a filesystem-backed ELF here. The file read fills
   * every consumed byte, so clearing this region during boot is wasted work,
   * especially through the physical SDRAM controller. */
  .noinit (NOLOAD) : {
    *(.noinit*)
  } > RAM

  /* Reserve one page for the initial M/S and supervisor-trap stacks. The
   * allocator owns only pages below this boundary, never either live stack. */
  __stack_bottom = ORIGIN(RAM) + LENGTH(RAM) - 4K;
  __stack_top = ORIGIN(RAM) + LENGTH(RAM);
  /* Both stacks descend, so divide the reserved page between them. The normal
   * supervisor stack owns the upper three quarters: role_execute's bounded
   * staging arrays alone need just over 2 KiB. A trap first subtracts its
   * 128-byte frame from the lower quarter's top; the C handler then grows
   * farther down through that quarter. Putting the frame itself at
   * __stack_bottom left the handler nowhere to grow and let schedule() write
   * into an allocator-owned page during a supervisor timer interrupt. */
  __trap_stack_top = __stack_bottom + ((__stack_top - __stack_bottom) / 4);
  _end = .;

  ASSERT(SIZEOF(.usertext) <= 4K,
         "user payload must fit one mapped 4 KiB page")
  ASSERT(_end <= __stack_bottom,
         "kernel image, BSS, and noinit overlap the reserved stack")
  ASSERT(_end - ORIGIN(RAM) <= AXOS_RESIDENT_BUDGET,
         "kernel exceeds the selected evolution tier's resident budget")

  /DISCARD/ : { *(.comment) *(.note*) *(.riscv.attributes) }
}
