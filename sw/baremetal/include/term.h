#pragma once

#include "platform.h"

/* Terminal contract for atomiX games and interactive programs.
 *
 * This is the floor tier of the one-game-per-board commitment
 * (docs/design-checklist.md): a program that is playable over the UART every
 * board already has, with no video, audio, or input component and no extra
 * block RAM.  A board with a display earns a richer contract later; nothing
 * here should change when that arrives.
 *
 * The contract is deliberately small:
 *
 *   input   one byte at a time from the 16550 receiver, polled.  A game may
 *           block for a key (turn-based) or poll and keep running (real-time);
 *           both use the same two calls.
 *   screen  a character grid addressed with ANSI escapes.  Every terminal
 *           anyone would use to talk to a board -- picocom, screen, minicom,
 *           PuTTY -- understands them, so "the screen" needs no driver on the
 *           FPGA side at all.
 *   package a game is an ordinary bare-metal payload under
 *           sw/baremetal/examples, selected with PROGRAM=<name>.  Adding a
 *           second game requires no platform work.
 *
 * The second half of the file is the real-time tier: a frame clock, absolute
 * addressing, and the accounting a game needs to report what a frame cost.  It
 * was added when the interactive tier landed and is deliberately part of the
 * same contract -- a second real-time game must not have to re-derive a
 * deadline from `mcycle`.
 *
 * Games must stay deterministic given a seed and an input sequence.  That is
 * what lets a game be tested in simulation by feeding keys through
 * UART_INPUT_FILE and comparing an exact final state, and it is why a game can
 * carry a pinned baseline like any other profile.  For a real-time game that
 * means taking at most one key per frame: the simulated UART hands bytes over
 * as fast as they are read, so a game that drained its input each frame would
 * swallow a whole key file in frame zero. */

/* 16550 line-status bit 0: receiver data ready. */
static inline int term_has_key(void) {
  return (mmio_read8(AX_UART_BASE + 5) & 0x01) != 0;
}

/* Blocking single-byte read.  Turn-based games use this directly. */
static inline char term_get_key(void) {
  while (!term_has_key()) {}
  return (char)mmio_read8(AX_UART_BASE);
}

/* Non-blocking read: returns 0 when no key is waiting, so a real-time game can
 * keep its own clock instead of stalling on input. */
static inline char term_poll_key(void) {
  return term_has_key() ? (char)mmio_read8(AX_UART_BASE) : (char)0;
}

/* Everything a game prints goes through one counted path.  The count is not
 * decoration: on a 115200-baud link a byte costs 2,170 cycles at 25 MHz, so
 * what a frame sends is usually the largest single term in what a frame costs,
 * and a panel that reports it turns "the machine feels responsive" into a
 * number.  It is per-translation-unit and a game that never reads it pays
 * nothing for it. */
static uint32_t term_tx_bytes __attribute__((unused));

static inline void term_putc(char c) {
  term_tx_bytes++;
  uart_putchar(c);
}

static inline void term_puts(const char *s) {
  while (*s) term_putc(*s++);
}

static inline void term_clear(void) { term_puts("\033[2J\033[H"); }
static inline void term_home(void) { term_puts("\033[H"); }

static inline void term_putu(uint32_t value) {
  char digits[10];
  int count = 0;
  if (!value) { term_putc('0'); return; }
  while (value) { digits[count++] = (char)('0' + value % 10u); value /= 10u; }
  while (count--) term_putc(digits[count]);
}

/* Right-aligned in a fixed field.  A live panel whose columns shift when a
 * number gains a digit is unreadable, and re-clearing the line to avoid that
 * would cost more bytes than the padding does. */
static inline void term_putu_pad(uint32_t value, uint32_t width) {
  uint32_t digits = 1, probe = value;
  while (probe >= 10u) { probe /= 10u; digits++; }
  while (width > digits) { term_putc(' '); width--; }
  term_putu(value);
}

static inline void term_puthex(uint32_t value) {
  term_puts("0x");
  for (int shift = 28; shift >= 0; shift -= 4)
    term_putc("0123456789abcdef"[(value >> shift) & 0xfu]);
}

/* Absolute addressing, 1-based like the escape itself.  This is what makes a
 * real-time game affordable: a frame costs the cells that changed rather than
 * a whole screen.  A full 40x20 redraw is 800 bytes, which at 115200 baud is
 * 69 ms -- a frame-rate ceiling of about 14 fps before the game has computed
 * anything at all. */
static inline void term_goto(uint32_t row, uint32_t col) {
  term_putc('\033');
  term_putc('[');
  term_putu(row);
  term_putc(';');
  term_putu(col);
  term_putc('H');
}

/* A blinking cursor parked wherever the last write landed is the difference
 * between a game and a scrolling log. */
static inline void term_cursor_hide(void) { term_puts("\033[?25l"); }
static inline void term_cursor_show(void) { term_puts("\033[?25h"); }

/* Deterministic RNG. A game seeded identically and given identical keys must
 * reach an identical state, on the board and in simulation alike. */
static inline uint32_t term_rand(uint32_t *state) {
  *state = *state * 1664525u + 1013904223u;
  return *state >> 16;
}

/* --------------------------------------------------------------------------
 * Frame clock
 *
 * A turn-based game needs nothing below this line: it blocks on a key and is
 * idle in between.  A real-time game needs a deadline, and on this path there
 * is no timer interrupt to give it one -- the UART is polled and a bare-metal
 * game does not take the CLINT.  So the frame clock is the game's own
 * deadline, measured against `mcycle`, and the accounting around it is part of
 * the contract rather than each game's own invention.
 *
 * The loop a game writes is:
 *
 *   term_frame_start(&clock, 12);
 *   for (;;) { input(); update(); draw(); term_frame_wait(&clock); }
 *
 * `work` afterwards is what the frame cost before it began waiting, which
 * against `period` is the honest measure of headroom.  A frame that overruns
 * is counted and the clock resynchronises rather than chasing a deadline that
 * has already passed, because a game that tries to catch up after one late
 * frame is late for every frame after it. */

#ifndef TERM_CPU_HZ
#define TERM_CPU_HZ 25000000u /* Tang Primer 25K; override per board. */
#endif

typedef struct {
  uint32_t period;  /* cycles per frame */
  uint32_t begin;   /* mcycle at the start of the current frame */
  uint32_t work;    /* cycles the last frame spent before waiting */
  uint32_t elapsed; /* cycles the last whole frame took, waiting included */
  uint32_t maxwork; /* worst work seen: the number a headroom claim rests on */
  uint32_t frames;
  uint32_t drops;   /* frames whose work exceeded the whole budget */
} term_frame_t;

static inline uint32_t term_cycles(void) {
  uint32_t value;
  __asm__ volatile("csrr %0, mcycle" : "=r"(value));
  return value;
}

static inline void term_frame_hz(term_frame_t *frame, uint32_t hz) {
  frame->period = hz ? TERM_CPU_HZ / hz : TERM_CPU_HZ;
}

static inline void term_frame_start(term_frame_t *frame, uint32_t hz) {
  frame->work = 0;
  frame->elapsed = 0;
  frame->maxwork = 0;
  frame->frames = 0;
  frame->drops = 0;
  term_frame_hz(frame, hz);
  frame->begin = term_cycles();
}

/* All comparisons are on unsigned differences, so a 32-bit `mcycle` wrapping
 * every 172 seconds at 25 MHz is not a special case.
 *
 * The wait spins rather than parking in `wfi`, which is the opposite of what
 * the rest of the machine does and is deliberate: nothing here arms a timer,
 * so no interrupt is pending to end the wait, and a hart parked with nothing
 * pending would hold until the next keystroke instead of the next frame -- it
 * would stop the game rather than idle it.  A game that wants a sleeping frame
 * clock needs the CLINT, which is a different contract than this one. */
static inline void term_frame_wait(term_frame_t *frame) {
  const uint32_t work = term_cycles() - frame->begin;
  frame->work = work;
  if (work > frame->maxwork) frame->maxwork = work;
  if (work >= frame->period) {
    frame->drops++;
    frame->elapsed = work;
    frame->begin = term_cycles();
  } else {
    while ((uint32_t)(term_cycles() - frame->begin) < frame->period) {}
    frame->elapsed = term_cycles() - frame->begin;
    frame->begin += frame->period;
  }
  frame->frames++;
}

/* --------------------------------------------------------------------------
 * Memory accounting
 *
 * `_end` and `__stack_top` come from link.ld: the image ends at one, the stack
 * grows down from the other, and everything between is free.  Reporting that
 * gap as a link-time constant would be a fact about the build; painting it and
 * scanning for what survived reports what the program has actually never
 * touched, which is what a panel claiming to show free memory should mean. */
extern char _end[];
extern char __stack_top[];

#define TERM_MEM_MARK 0xa5a5a5a5u

/* Address of the first word the stack has taken back, so the usual answer
 * costs one load rather than a walk. */
static uintptr_t term_mem_boundary __attribute__((unused));

/* Call once, early.  Everything above the current stack pointer is left alone,
 * and the skirt below it keeps this function's own frame out of the paint. */
static inline void term_mem_paint(void) {
  uint32_t sp;
  __asm__ volatile("mv %0, sp" : "=r"(sp));
  volatile uint32_t *word = (volatile uint32_t *)(((uintptr_t)_end + 3u) & ~3u);
  const uintptr_t limit = (uintptr_t)((sp - 256u) & ~3u);
  while ((uintptr_t)word < limit) *word++ = TERM_MEM_MARK;
  term_mem_boundary = limit;
}

/* Free memory only ever shrinks here, and the stack that consumes it is one
 * contiguous run down from the top, so if the last painted word is still
 * painted nothing has moved.  That matters: a panel redrawn every frame cannot
 * spend a walk of the whole gap on one field, and on a 128 KiB link that walk
 * was three times the cost of the rest of the frame put together. */
static inline uint32_t term_mem_free(void) {
  const uintptr_t base = ((uintptr_t)_end + 3u) & ~3u;
  const uintptr_t top = (uintptr_t)__stack_top;
  if (term_mem_boundary > base &&
      *(const volatile uint32_t *)(term_mem_boundary - 4u) == TERM_MEM_MARK)
    return (uint32_t)(term_mem_boundary - base);
  uintptr_t scan = base;
  while (scan < top && *(const volatile uint32_t *)scan == TERM_MEM_MARK)
    scan += 4u;
  term_mem_boundary = scan;
  return (uint32_t)(scan - base);
}
