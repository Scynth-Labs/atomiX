/* Initial aXos entry. Reset is M-mode; m_setup in kernel.c makes the first
 * identity Sv32 map and leaves MPP=S/mepc=s_entry. mret then starts the
 * supervisor kernel with translation genuinely active. */
  .section .text.init, "ax", @progbits
  .globl _start
  .type _start, @function
_start:
  .option push
  .option norelax
  la sp, __stack_top
  .option pop

  la t0, __bss_start
  la t1, __bss_end
1:
  bgeu t0, t1, 2f
  sw zero, 0(t0)
  addi t0, t0, 4
  j 1b
2:
  call m_setup
  mret

  .globl s_entry
  .type s_entry, @function
s_entry:
  .option push
  .option norelax
  la sp, __stack_top
  la t0, supervisor_trap_entry
  /* Keep the bootstrap S-mode call stack and its asynchronous trap frame
   * disjoint. Both live in the reserved final RAM page, while the allocator
   * stops below __stack_bottom. */
  la t1, __trap_stack_top
  .option pop
  csrw stvec, t0
  csrw sscratch, t1
  csrsi sstatus, 2               /* enable delegated supervisor interrupts */
  call kmain
3:
  wfi
  j 3b

  .size _start, . - _start
