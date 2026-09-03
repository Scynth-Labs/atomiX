/* User programs are linked at USER_CODE_VA (0x40000000), the base of the 4 MiB
 * region the Sv32 user address space maps.  Segments are page-aligned so the
 * loader can give each its own permissions; without that, .text and .rodata
 * would share a page and have to share a permission set.
 *
 * The explicit PHDRS are what make that true rather than merely intended.
 * Page-aligning the sections is not enough on its own: ld assigns sections to
 * segments by flag compatibility, and .rodata (A) is compatible with .text
 * (AX), so it was being placed in the same R+E segment and mapped *executable*
 * -- the loader faithfully applying a p_flags that said so.  The alignment
 * bought nothing, because a segment, not a page, is the unit the loader gets
 * permissions from.  Naming three segments here separates them for real, and
 * makes the loader's per-segment mapping something the program actually
 * exercises rather than something its comments claim. */
OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY { USER (rwx) : ORIGIN = 0x40000000, LENGTH = 4M - 4K }

PHDRS
{
  text   PT_LOAD FLAGS(5);  /* R+X: code only */
  rodata PT_LOAD FLAGS(4);  /* R:   constants, never executable */
  data   PT_LOAD FLAGS(6);  /* R+W: .data and the .bss tail */
}

SECTIONS
{
  .text : ALIGN(4K) { *(.text._start) *(.text*) } > USER :text
  .rodata : ALIGN(4K) { *(.rodata*) *(.srodata*) } > USER :rodata
  .data : ALIGN(4K) { *(.data*) *(.sdata*) } > USER :data
  .bss : ALIGN(16) { *(.bss*) *(.sbss*) *(COMMON) } > USER :data
  /DISCARD/ : { *(.comment) *(.note*) *(.riscv.attributes) }
}
