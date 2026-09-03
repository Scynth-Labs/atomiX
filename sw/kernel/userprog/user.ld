/* User programs are linked at USER_CODE_VA (0x40000000), the base of the 4 MiB
 * region the Sv32 user address space maps.
 *
 * Three things are load-bearing here, and each fails silently if dropped.
 *
 * The explicit PHDRS are what give the segments separate permissions.  Aligning
 * sections to a page is not enough: ld assigns sections to segments by flag
 * compatibility, and .rodata (A) is compatible with .text (AX), so it was being
 * placed in the same R+E segment and mapped *executable* -- the loader
 * faithfully applying a p_flags that said so.  A segment, not a page, is the
 * unit the loader takes permissions from.
 *
 * FILEHDR PHDRS put the ELF header and program headers inside the first
 * segment rather than in a page of their own.  The embedded copy of this image
 * is the single largest item in the kernel (see docs/design-checklist.md), and
 * a header page that no segment covers is a whole 4 KiB the kernel carries and
 * the loader skips.  It also makes AT_PHDR meaningful for the first time: the
 * loader derives it from whichever segment covers e_phoff, and previously no
 * segment did.
 *
 * .rodata and .data still start on page boundaries because separate
 * permissions need separate pages -- that part of the alignment was always
 * real, and it is what bounds how small this image can get. */
OUTPUT_ARCH(riscv)
ENTRY(_start)

USER_BASE = 0x40000000;
USER_LIMIT = USER_BASE + 4M - 4K;

PHDRS
{
  text   PT_LOAD FILEHDR PHDRS FLAGS(5);  /* R+X: headers and code */
  rodata PT_LOAD FLAGS(4);                /* R:   constants, never executable */
  data   PT_LOAD FLAGS(6);                /* R+W: .data and the .bss tail */
}

SECTIONS
{
  . = USER_BASE + SIZEOF_HEADERS;
  .text : { *(.text._start) *(.text*) } :text
  . = ALIGN(4K);
  .rodata : { *(.rodata*) *(.srodata*) } :rodata
  . = ALIGN(4K);
  .data : { *(.data*) *(.sdata*) } :data
  .bss : ALIGN(16) { *(.bss*) *(.sbss*) *(COMMON) } :data
  /DISCARD/ : { *(.comment) *(.note*) *(.riscv.attributes) }
  ASSERT(. <= USER_LIMIT, "user image overflows the mapped user region")
}
