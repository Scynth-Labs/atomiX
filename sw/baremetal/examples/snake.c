/* Snake for atomiX: the interactive-tier game, and the Tang Primer 25K's.
 *
 * 2048 proved the platform can host a game at all.  It could not prove the
 * machine stays *responsive*, because a turn-based game never has to be: it
 * blocks on a key, redraws once, and is idle until the next one.  Nothing in
 * it can be late.
 *
 * This one has a deadline.  It redraws on a frame clock whether or not
 * anything was typed, reads input without stalling, and reports what each
 * frame actually cost -- so "is this pleasant to use" becomes a number
 * standing next to the score instead of an impression.  That is the whole
 * argument for the interactive tier: every accelerator result in this project
 * answers "did the output match", and none of them answers this.
 *
 * Three things make it work on a 25 MHz core with a polled UART and no timer
 * interrupt:
 *
 *   the clock   is the game's own, measured against `mcycle` (term.h).  There
 *               is no tick to sleep on, so a frame ends when the game says it
 *               does.
 *   the redraw  costs the cells that changed.  Repainting the whole 28x14
 *               field the way this draws it -- an address escape per cell --
 *               is about 4 KB: 47 ms on the 921600-baud loader profile, and
 *               370 ms at 115200, which is four whole frames to redraw one.
 *               A moving snake changes three cells.  The game does not know
 *               the baud rate and does not need to; the panel reports the
 *               cycles, which is the same fact without the assumption.
 *   the panel   reports frame time, work against budget, bytes sent, and free
 *               RAM.  On the board the dominant cost is the serial link, and
 *               the panel is what makes that visible rather than mysterious.
 *
 * Determinism is kept the same way 2048 keeps it, and it matters more here
 * because a real-time game has more ways to drift: the RNG is seeded with a
 * constant, and the game consumes at most one key per frame.  That makes a key
 * script a frame-by-frame input tape -- one byte per frame, '.' for a frame
 * with nothing pressed -- so `make -C sw/baremetal check-snake` replays it and
 * requires the exact final state.
 *
 * Controls: w/a/s/d or arrows steer, p pause, r restart, q quit.
 */
#include "platform.h"
#include "term.h"

/* Frames per second at TERM_CPU_HZ before the level scaling below.  The check
 * target overrides it: at the shipped rate one frame is 2.08M cycles, so a
 * hundred-frame replay would be 200M simulated cycles for logic that does not
 * depend on the constant.  What the check verifies is the pacing mechanism --
 * that frames land on their deadline and none overruns -- which holds at any
 * rate the machine can actually meet. */
#ifndef AX_SNAKE_FRAME_HZ
#define AX_SNAKE_FRAME_HZ 12u
#endif

#define AX_SNAKE_SEED 0x51a4e01u

#define FIELD_W 28
#define FIELD_H 14
#define CELLS (FIELD_W * FIELD_H)

/* Screen layout, in 1-based terminal coordinates.  A cell is two columns wide
 * because a terminal character is about twice as tall as it is wide, and a
 * snake on a stretched grid moves visibly faster sideways than it does up. */
#define COL_LEFT 3u
#define ROW_TITLE 1u
#define ROW_TOP 3u
#define ROW_BOT (ROW_TOP + FIELD_H + 1u)
#define ROW_MSG (ROW_BOT + 1u)
#define ROW_PANEL (ROW_BOT + 2u)
#define ROW_HELP (ROW_BOT + 5u)

enum { EMPTY = 0, BODY = 1, HEAD = 2, FOOD = 3 };
enum { DIR_UP = 0, DIR_DOWN = 1, DIR_LEFT = 2, DIR_RIGHT = 3 };

static const int drow[4] = {-1, 1, 0, 0};
static const int dcol[4] = {0, 0, -1, 1};
static const char *const glyph[4] = {"  ", "[]", "@@", "**"};

static uint8_t cell[CELLS];
static uint8_t shown[CELLS];
/* The snake as a ring of cell indices, oldest at `tail`, newest at `head`.
 * Growing is not moving the whole body: it is one push and one skipped pop. */
static uint16_t ring[CELLS];
static uint16_t ring_head, ring_tail, snake_len;

static uint32_t score, foods, level, rng;
static int dir, next_dir, alive, paused;
static term_frame_t clk;
static uint32_t frame_bytes, tx_mark;
static int drawn_state = -1;

/* Folded once per frame and never reset, so one pinned number covers the whole
 * session rather than its last picture.  A restart resets the game to a state
 * that owes nothing to what came before it, so a final-state checksum would
 * quietly stop testing everything before the last `r`. */
static uint32_t trace = 2166136261u;

static void apply_speed(void) {
  /* Multiplicative, so the difficulty curve is the same shape whatever the
   * base rate is -- including the compressed one the check builds. */
  const uint32_t base = TERM_CPU_HZ / (uint32_t)AX_SNAKE_FRAME_HZ;
  const uint32_t steps = level > 8u ? 8u : level;
  clk.period = base * 100u / (100u + 15u * steps);
}

static void ring_push(uint16_t pos) {
  ring_head = (uint16_t)((ring_head + 1u) % CELLS);
  ring[ring_head] = pos;
  snake_len++;
}

static void spawn_food(void) {
  uint32_t empties = 0;
  for (uint32_t i = 0; i < CELLS; ++i)
    if (cell[i] == EMPTY) empties++;
  if (!empties) return; /* board full: the win case, handled by the caller */
  uint32_t pick = term_rand(&rng) % empties;
  for (uint32_t i = 0; i < CELLS; ++i) {
    if (cell[i] != EMPTY) continue;
    if (pick == 0) {
      cell[i] = FOOD;
      return;
    }
    pick--;
  }
}

static void reset(void) {
  for (uint32_t i = 0; i < CELLS; ++i) cell[i] = EMPTY;
  rng = AX_SNAKE_SEED;
  score = 0;
  foods = 0;
  level = 0;
  snake_len = 0;
  ring_head = (uint16_t)(CELLS - 1);
  ring_tail = 0;
  dir = DIR_RIGHT;
  next_dir = DIR_RIGHT;
  alive = 1;
  paused = 0;
  const uint32_t row = FIELD_H / 2u;
  for (uint32_t i = 0; i < 4u; ++i) {
    const uint16_t pos = (uint16_t)(row * FIELD_W + 3u + i);
    ring_push(pos);
    cell[pos] = BODY;
  }
  cell[ring[ring_head]] = HEAD;
  spawn_food();
  apply_speed();
}

static void step(void) {
  dir = next_dir;
  const int head_pos = (int)ring[ring_head];
  const int row = head_pos / FIELD_W + drow[dir];
  const int col = head_pos % FIELD_W + dcol[dir];
  if (row < 0 || row >= FIELD_H || col < 0 || col >= FIELD_W) {
    alive = 0;
    return;
  }
  const int next_pos = row * FIELD_W + col;
  const int eat = (cell[next_pos] == FOOD);
  const uint16_t tail_pos = ring[ring_tail];

  /* Moving into the cell the tail is about to leave is legal, so the tail is
   * only retired after that exception has been taken into account -- checking
   * collision against the pre-move body would kill a snake chasing itself at
   * full length, which is the one move every player expects to survive. */
  if (cell[next_pos] == BODY && !(next_pos == (int)tail_pos && !eat)) {
    alive = 0;
    return;
  }
  if (!eat) {
    cell[tail_pos] = EMPTY;
    ring_tail = (uint16_t)((ring_tail + 1u) % CELLS);
    snake_len--;
  }
  cell[head_pos] = BODY;
  cell[next_pos] = HEAD;
  ring_push((uint16_t)next_pos);

  if (eat) {
    score += 10u * (level + 1u);
    foods++;
    if (foods % 4u == 0u) {
      level++;
      apply_speed();
    }
    spawn_food();
  }
}

static void trace_fold(void) {
  const uint32_t facts[5] = {
      ring[ring_head], snake_len, score,
      (level << 2) | ((uint32_t)paused << 1) | (uint32_t)alive, foods};
  for (uint32_t i = 0; i < 5u; ++i) {
    trace ^= facts[i];
    trace *= 16777619u;
  }
}

static uint32_t checksum(void) {
  uint32_t sum = 2166136261u;
  for (uint32_t i = 0; i < CELLS; ++i) {
    sum ^= cell[i];
    sum *= 16777619u;
  }
  const uint32_t tail[5] = {score, snake_len, level, rng, trace};
  for (uint32_t i = 0; i < 5u; ++i) {
    sum ^= tail[i];
    sum *= 16777619u;
  }
  return sum;
}

static void draw_border(void) {
  term_putc('+');
  for (uint32_t i = 0; i < 2u * FIELD_W; ++i) term_putc('-');
  term_putc('+');
}

static void draw_static(void) {
  term_clear();
  term_cursor_hide();
  term_goto(ROW_TITLE, COL_LEFT);
  term_puts("atomiX snake");
  term_goto(ROW_TOP, COL_LEFT);
  draw_border();
  for (uint32_t r = 0; r < FIELD_H; ++r) {
    term_goto(ROW_TOP + 1u + r, COL_LEFT);
    term_putc('|');
    term_goto(ROW_TOP + 1u + r, COL_LEFT + 1u + 2u * FIELD_W);
    term_putc('|');
  }
  term_goto(ROW_BOT, COL_LEFT);
  draw_border();
  term_goto(ROW_HELP, COL_LEFT);
  term_puts("w/a/s/d move   p pause   l redraw   r restart   q quit");
  for (uint32_t i = 0; i < CELLS; ++i) shown[i] = 0xffu;
  drawn_state = -1;
}

static void draw_cells(void) {
  for (uint32_t i = 0; i < CELLS; ++i) {
    if (shown[i] == cell[i]) continue;
    term_goto(ROW_TOP + 1u + i / FIELD_W, COL_LEFT + 1u + 2u * (i % FIELD_W));
    term_puts(glyph[cell[i]]);
    shown[i] = cell[i];
  }
}

/* Tenths of a millisecond, which is the resolution a frame budget is actually
 * discussed in.  There is no FPU and no printf here; there does not need to
 * be. */
static void put_ms(uint32_t cycles) {
  const uint32_t tenths = cycles / (TERM_CPU_HZ / 10000u);
  term_putu_pad(tenths / 10u, 4);
  term_putc('.');
  term_putc((char)('0' + tenths % 10u));
}

static void put_pct(uint32_t part, uint32_t whole) {
  const uint32_t unit = whole / 1000u;
  const uint32_t tenths = unit ? part / unit : 0u;
  term_putu_pad(tenths / 10u, 3);
  term_putc('.');
  term_putc((char)('0' + tenths % 10u));
}

/* The htop-style panel.  Every number here is measured on the machine that is
 * running the game, so the same payload in a browser tab and on the Dock at
 * 25 MHz can be compared field by field rather than by assertion. */
static void draw_panel(void) {
  term_goto(ROW_PANEL, COL_LEFT);
  term_puts("score ");
  term_putu_pad(score, 5);
  term_puts("   len ");
  term_putu_pad(snake_len, 4);
  term_puts("   level ");
  term_putu_pad(level + 1u, 2);
  term_puts("   frame ");
  term_putu_pad(clk.frames, 6);

  term_goto(ROW_PANEL + 1u, COL_LEFT);
  term_puts("frame ");
  put_ms(clk.elapsed);
  term_puts("ms   work ");
  term_putu_pad(clk.work, 8);
  term_puts("cyc (");
  put_pct(clk.work, clk.period);
  term_puts("%)   drops ");
  term_putu_pad(clk.drops, 4);

  term_goto(ROW_PANEL + 2u, COL_LEFT);
  term_puts("uart ");
  term_putu_pad(frame_bytes, 4);
  term_puts("B/frame   free ");
  term_putu_pad(term_mem_free(), 6);
  term_puts("B   budget ");
  term_putu_pad(clk.period, 8);
  term_puts("cyc");
}

static void draw_message(void) {
  const int state = alive ? (paused ? 1 : 0) : 2;
  if (state == drawn_state) return;
  drawn_state = state;
  term_goto(ROW_MSG, COL_LEFT);
  term_puts(state == 0   ? "                                        "
            : state == 1 ? "PAUSED -- p to resume                   "
                         : "GAME OVER -- r to restart, q to quit    ");
}

__attribute__((noreturn)) static void finish(const char *why) {
  term_cursor_show();
  term_goto(ROW_HELP + 1u, 1u);
  term_puts("snake: ");
  term_puts(why);
  term_puts(" score=");
  term_putu(score);
  term_puts(" len=");
  term_putu(snake_len);
  term_puts(" level=");
  term_putu(level + 1u);
  term_puts(" foods=");
  term_putu(foods);
  term_puts(" frames=");
  term_putu(clk.frames);
  term_puts(" drops=");
  term_putu(clk.drops);
  term_puts(" maxwork=");
  term_putu(clk.maxwork);
  term_puts(" period=");
  term_putu(clk.period);
  term_puts(" checksum=");
  term_puthex(checksum());
  term_puts("\r\n");
  uart_drain();
  test_finish(0);
}

/* An arrow key is ESC '[' followed by a letter.  At 115200 baud the next byte
 * of that sequence is 87 us away, so waiting a bounded moment for it costs a
 * fraction of a frame and beats the alternative of spreading one keypress
 * across three frames -- which at 12 fps is a quarter-second of steering lag.
 * The bound is what keeps it safe: a lone ESC returns nothing rather than
 * hanging the frame clock. */
static int wait_byte(char *out) {
  const uint32_t start = term_cycles();
  while ((uint32_t)(term_cycles() - start) < TERM_CPU_HZ / 1000u) {
    if (term_has_key()) {
      *out = term_get_key();
      return 1;
    }
  }
  return 0;
}

/* At most one key per frame, which is both the right game rule -- a second
 * direction change inside one frame could only cancel the first -- and what
 * makes a key script a frame-by-frame tape. */
static char read_key(void) {
  char key = term_poll_key();
  if (key != 0x1b) return key;
  char next = 0;
  if (!wait_byte(&next) || next != '[') return 0;
  if (!wait_byte(&next)) return 0;
  switch (next) {
    case 'A': return 'w';
    case 'B': return 's';
    case 'C': return 'd';
    case 'D': return 'a';
    default: return 0;
  }
}

static void steer(int want) {
  /* A reversal would drive the head straight into the neck, so it is ignored
   * rather than fatal.  `dir` and not `next_dir` is the reference: two turns
   * inside one frame must not compose into one. */
  if ((dir ^ 1) == want) return;
  next_dir = want;
}

int main(void) {
  term_mem_paint();
  reset();
  draw_static();
  draw_cells();
  draw_message();
  draw_panel();
  /* The clock starts after the first whole screen, not before it: painting an
   * empty field and 392 cells is setup, and charging it to frame zero would
   * report a drop that says nothing about how the game runs. */
  tx_mark = term_tx_bytes;
  term_frame_start(&clk, AX_SNAKE_FRAME_HZ);
  apply_speed();

  for (;;) {
    frame_bytes = term_tx_bytes - tx_mark;
    tx_mark = term_tx_bytes;

    switch (read_key()) {
      case 'w': case 'W': steer(DIR_UP); break;
      case 's': case 'S': steer(DIR_DOWN); break;
      case 'a': case 'A': steer(DIR_LEFT); break;
      case 'd': case 'D': steer(DIR_RIGHT); break;
      case 'p': case 'P': paused = !paused; break;
      case 'l': case 'L': case 0x0c:
        /* Repaint everything.  A program is uploaded to a running board and
         * the terminal is attached afterwards, so the first screen is usually
         * already gone by the time anyone is looking -- and a game that only
         * ever draws what changed can never recover from that on its own.
         * It costs a full screen, which the panel will report as a dropped
         * frame on a slow link; that is the honest price of asking for it. */
        draw_static();
        break;
      case 'r': case 'R':
        /* No full repaint: the borders and the help line are still on the
         * screen, and the diff below emits exactly the cells the new game
         * differs in.  Redrawing everything would cost 4 KB -- four whole
         * frames on the board -- to produce the same picture. */
        reset();
        break;
      case 'q': case 'Q':
        finish("QUIT");
      default: break;
    }

    if (alive && !paused) step();
    trace_fold();
    draw_cells();
    draw_message();
    draw_panel();
    if (alive && snake_len >= CELLS) finish("WIN");
    term_frame_wait(&clk);
  }
}
