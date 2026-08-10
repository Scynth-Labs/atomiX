/* 2048 for atomiX: the first shipped game, terminal tier.
 *
 * Playable over the UART that every atomiX board already has -- no video,
 * audio, or input component, and no block RAM beyond the payload itself. That
 * is the point: the floor tier of the one-game-per-board commitment has to run
 * on the hardware someone already owns, or the commitment is not real.
 *
 * 2048 was chosen over something like snake because it is turn-based. A polled
 * UART with no timer interrupt makes real-time input awkward, while a
 * turn-based game reads exactly one key per move and is therefore perfectly
 * deterministic: the same seed and the same keys produce the same board on the
 * board and in simulation alike. That is what makes it testable rather than
 * merely demonstrable -- `make -C sw/baremetal check-game2048` replays a fixed
 * key sequence through UART_INPUT_FILE and checks the exact final state.
 *
 * Controls: w/a/s/d or arrow keys to move, r to restart, q to quit.
 */
#include "platform.h"
#include "term.h"

#define N 4
#define WIN_TILE 2048u

static uint32_t board[N][N];
static uint32_t score;
static uint32_t rng;

static void spawn(void) {
  uint32_t empty[N * N];
  uint32_t count = 0;
  for (uint32_t i = 0; i < N * N; ++i)
    if (!board[i / N][i % N]) empty[count++] = i;
  if (!count) return;
  uint32_t pick = empty[term_rand(&rng) % count];
  /* Classic 2048 odds: one tile in ten starts at 4. */
  board[pick / N][pick % N] = (term_rand(&rng) % 10u) ? 2u : 4u;
}

static void reset(uint32_t seed) {
  for (uint32_t r = 0; r < N; ++r)
    for (uint32_t c = 0; c < N; ++c) board[r][c] = 0;
  score = 0;
  rng = seed;
  spawn();
  spawn();
}

/* Collapse one line towards index 0 and report whether anything moved. Every
 * direction is this operation over a different traversal, which keeps the merge
 * rule in exactly one place. */
static int slide_line(uint32_t *line) {
  uint32_t packed[N];
  uint32_t count = 0, moved = 0;
  for (uint32_t i = 0; i < N; ++i)
    if (line[i]) packed[count++] = line[i];
  for (uint32_t i = count; i < N; ++i) packed[i] = 0;

  for (uint32_t i = 0; i + 1 < count; ++i) {
    if (packed[i] && packed[i] == packed[i + 1]) {
      packed[i] *= 2u;
      score += packed[i];
      for (uint32_t j = i + 1; j + 1 < N; ++j) packed[j] = packed[j + 1];
      packed[N - 1] = 0;
      count--;
    }
  }
  for (uint32_t i = 0; i < N; ++i) {
    if (line[i] != packed[i]) moved = 1;
    line[i] = packed[i];
  }
  return (int)moved;
}

/* dir: 0 up, 1 down, 2 left, 3 right. */
static int move(uint32_t dir) {
  uint32_t line[N];
  int moved = 0;
  for (uint32_t k = 0; k < N; ++k) {
    for (uint32_t i = 0; i < N; ++i) {
      uint32_t r = (dir == 0) ? i : (dir == 1) ? (N - 1 - i) : k;
      uint32_t c = (dir == 2) ? i : (dir == 3) ? (N - 1 - i) : k;
      line[i] = board[r][c];
    }
    moved |= slide_line(line);
    for (uint32_t i = 0; i < N; ++i) {
      uint32_t r = (dir == 0) ? i : (dir == 1) ? (N - 1 - i) : k;
      uint32_t c = (dir == 2) ? i : (dir == 3) ? (N - 1 - i) : k;
      board[r][c] = line[i];
    }
  }
  return moved;
}

static int can_move(void) {
  for (uint32_t r = 0; r < N; ++r)
    for (uint32_t c = 0; c < N; ++c) {
      if (!board[r][c]) return 1;
      if (c + 1 < N && board[r][c] == board[r][c + 1]) return 1;
      if (r + 1 < N && board[r][c] == board[r + 1][c]) return 1;
    }
  return 0;
}

static uint32_t checksum(void) {
  uint32_t sum = 2166136261u;
  for (uint32_t r = 0; r < N; ++r)
    for (uint32_t c = 0; c < N; ++c) {
      sum ^= board[r][c];
      sum *= 16777619u;
    }
  return sum ^ score;
}

static void draw(void) {
  term_home();
  uart_puts("atomiX 2048   score ");
  term_putu(score);
  uart_puts("      \r\n\r\n");
  for (uint32_t r = 0; r < N; ++r) {
    uart_puts("    +------+------+------+------+\r\n    ");
    for (uint32_t c = 0; c < N; ++c) {
      uart_puts("|");
      uint32_t v = board[r][c];
      /* Centre each value in a six-column cell without a printf. */
      uint32_t digits = 1, probe = v;
      while (probe >= 10u) { probe /= 10u; digits++; }
      if (!v) digits = 0;
      uint32_t pad = 6u - digits;
      for (uint32_t i = 0; i < pad / 2u; ++i) uart_putchar(' ');
      if (v) term_putu(v);
      for (uint32_t i = 0; i < pad - pad / 2u; ++i) uart_putchar(' ');
    }
    uart_puts("|\r\n");
  }
  uart_puts("    +------+------+------+------+\r\n\r\n");
  uart_puts("    w/a/s/d move   r restart   q quit\r\n");
}

int main(void) {
  reset(0x2048u);
  term_clear();
  draw();

  for (;;) {
    char key = term_get_key();
    int dir = -1;
    switch (key) {
      case 'w': case 'W': dir = 0; break;
      case 's': case 'S': dir = 1; break;
      case 'a': case 'A': dir = 2; break;
      case 'd': case 'D': dir = 3; break;
      case 0x1b:
        /* Arrow keys arrive as ESC [ A..D; consume the pair and map it. */
        if (term_get_key() == '[') {
          switch (term_get_key()) {
            case 'A': dir = 0; break;
            case 'B': dir = 1; break;
            case 'D': dir = 2; break;
            case 'C': dir = 3; break;
            default: break;
          }
        }
        break;
      case 'r': case 'R':
        reset(0x2048u);
        term_clear();
        draw();
        continue;
      case 'q': case 'Q':
        uart_puts("\r\n2048: QUIT score=");
        term_putu(score);
        uart_puts(" checksum=");
        term_puthex(checksum());
        uart_puts("\r\n");
        uart_drain();
        test_finish(0);
      default: break;
    }
    if (dir < 0) continue;

    if (move((uint32_t)dir)) {
      spawn();
      draw();
      uint32_t best = 0;
      for (uint32_t r = 0; r < N; ++r)
        for (uint32_t c = 0; c < N; ++c)
          if (board[r][c] > best) best = board[r][c];
      if (best >= WIN_TILE) {
        uart_puts("\r\n    you reached 2048. keep going, or q to stop.\r\n");
      }
    }
    if (!can_move()) {
      uart_puts("\r\n2048: GAME OVER score=");
      term_putu(score);
      uart_puts(" checksum=");
      term_puthex(checksum());
      uart_puts("\r\n");
      uart_drain();
      test_finish(0);
    }
  }
}
