#!/usr/bin/env python3
"""Generate the deterministic key tape that check-snake replays.

`sw/baremetal/examples/snake.c` takes at most one key per frame, so a key file
is a frame-by-frame input tape: byte N is what was pressed during frame N, and
'.' is a frame with nothing pressed.  That is what makes a real-time game
replayable at all, but it also means a useful tape cannot be written by hand --
where the food lands is a function of the game's own RNG, so steering into it
requires knowing what the game will do.

So this is a host model of the same rules, used to plan the tape and to predict
the exact state the run must end in.  It is not a second implementation the
game depends on: nothing links against it, and the pinned checksum in the
Makefile comes from the RTL run, not from here.  It exists so the tape is
reproducible and extendable instead of magic, and it is a cross-check --
a disagreement between this model and the machine is a real finding either way.

    python3 make_snake_tape.py            # rewrite tests-snake-keys.txt
    python3 make_snake_tape.py --dry-run  # just print the prediction

Keep the constants below in step with snake.c if that file's geometry, seed, or
scoring changes; the script re-derives everything else.
"""

import argparse
import collections
import pathlib

# --- constants mirrored from snake.c ---------------------------------------
W, H = 28, 14
CELLS = W * H
SEED = 0x51A4E01
EMPTY, BODY, HEAD, FOOD = 0, 1, 2, 3
UP, DOWN, LEFT, RIGHT = 0, 1, 2, 3
DROW = (-1, 1, 0, 0)
DCOL = (0, 0, -1, 1)
KEY = {UP: "w", DOWN: "s", LEFT: "a", RIGHT: "d"}
MASK = 0xFFFFFFFF

# --- tape shape -------------------------------------------------------------
FOODS_BEFORE_PAUSE = 3   # eat, grow, and cross a level boundary at 4
PAUSE_FRAMES = 3         # frames that must pass with the game frozen
FOODS_TOTAL = 6          # then eat enough to raise the level again
IDLE_AFTER_DEATH = 2     # frames in the game-over state before restarting
FOODS_AFTER_RESTART = 1  # prove a restarted game plays, not just redraws


class Snake:
    """The rules of snake.c, and nothing else."""

    def __init__(self):
        self.cell = [EMPTY] * CELLS
        self.rng = SEED
        self.score = 0
        self.foods = 0
        self.level = 0
        self.dir = RIGHT
        self.next_dir = RIGHT
        self.alive = True
        self.paused = False
        self.body = collections.deque()
        if not hasattr(self, "trace"):  # session-scoped: a restart does not clear it
            self.trace = 2166136261
        row = H // 2
        for i in range(4):
            pos = row * W + 3 + i
            self.body.append(pos)
            self.cell[pos] = BODY
        self.cell[self.body[-1]] = HEAD
        self.spawn_food()

    def rand(self):
        self.rng = (self.rng * 1664525 + 1013904223) & MASK
        return self.rng >> 16

    def spawn_food(self):
        empties = sum(1 for value in self.cell if value == EMPTY)
        if not empties:
            return
        pick = self.rand() % empties
        for i, value in enumerate(self.cell):
            if value != EMPTY:
                continue
            if pick == 0:
                self.cell[i] = FOOD
                return
            pick -= 1

    def steer(self, want):
        if (self.dir ^ 1) == want:
            return
        self.next_dir = want

    def step(self):
        self.dir = self.next_dir
        head = self.body[-1]
        row, col = divmod(head, W)
        row += DROW[self.dir]
        col += DCOL[self.dir]
        if not (0 <= row < H and 0 <= col < W):
            self.alive = False
            return
        nxt = row * W + col
        eat = self.cell[nxt] == FOOD
        tail = self.body[0]
        if self.cell[nxt] == BODY and not (nxt == tail and not eat):
            self.alive = False
            return
        if not eat:
            self.cell[tail] = EMPTY
            self.body.popleft()
        self.cell[head] = BODY
        self.cell[nxt] = HEAD
        self.body.append(nxt)
        if eat:
            self.score += 10 * (self.level + 1)
            self.foods += 1
            if self.foods % 4 == 0:
                self.level += 1
            self.spawn_food()

    def frame(self, key):
        """One iteration of the game's main loop.  Returns False on quit."""
        if key in "qQ":
            return False
        if key in "wW":
            self.steer(UP)
        elif key in "sS":
            self.steer(DOWN)
        elif key in "aA":
            self.steer(LEFT)
        elif key in "dD":
            self.steer(RIGHT)
        elif key in "pP":
            self.paused = not self.paused
        elif key in "rR":
            self.__init__()
        if self.alive and not self.paused:
            self.step()
        self.fold()
        return True

    def fold(self):
        facts = (self.body[-1], len(self.body), self.score,
                 (self.level << 2) | (int(self.paused) << 1) | int(self.alive),
                 self.foods)
        for fact in facts:
            self.trace = ((self.trace ^ fact) * 16777619) & MASK

    def checksum(self):
        value = 2166136261
        for content in self.cell:
            value = ((value ^ content) * 16777619) & MASK
        for extra in (self.score, len(self.body), self.level, self.rng,
                      self.trace):
            value = ((value ^ extra) * 16777619) & MASK
        return value

    def route(self):
        """First direction of a shortest path from the head to the food.

        Breadth-first over the cells the body does not occupy.  The body will
        have moved by the time the head arrives, so this is an approximation --
        which is fine: the tape is whatever it produces, and the game is the
        authority on what that tape does.
        """
        head = self.body[-1]
        goal = self.cell.index(FOOD) if FOOD in self.cell else None
        if goal is None:
            return None
        seen = {head: None}
        queue = collections.deque([head])
        while queue:
            pos = queue.popleft()
            if pos == goal:
                break
            row, col = divmod(pos, W)
            for direction in (UP, DOWN, LEFT, RIGHT):
                r, c = row + DROW[direction], col + DCOL[direction]
                if not (0 <= r < H and 0 <= c < W):
                    continue
                nxt = r * W + c
                if nxt in seen or self.cell[nxt] == BODY:
                    continue
                seen[nxt] = (pos, direction)
                queue.append(nxt)
        if goal not in seen:
            return None
        pos, first = goal, None
        while seen[pos] is not None:
            pos, first = seen[pos]
        return first


def plan():
    """Play the model, recording the key each frame would have received."""
    game = Snake()
    tape = []

    def run(key):
        tape.append(key)
        game.frame(key)

    def chase(until_foods):
        # A bounded chase: the frame budget is generous, but a model that can
        # no longer reach the food must not spin here forever.
        for _ in range(CELLS * 4):
            if game.foods >= until_foods or not game.alive:
                return
            want = game.route()
            if want is None or want == game.dir:
                run(".")
            else:
                run(KEY[want])

    chase(FOODS_BEFORE_PAUSE)
    run("p")
    for _ in range(PAUSE_FRAMES):
        run(".")
    run("p")
    chase(FOODS_TOTAL)

    # Stop steering and let it run into a wall: dying is a state the game has
    # to handle, and a tape that never reaches it never tests it.
    while game.alive:
        run(".")
    for _ in range(IDLE_AFTER_DEATH):
        run(".")
    run("r")
    # A restarted game has to play, not merely repaint, so the tape eats once
    # more afterwards.  Everything before this point is still covered: the
    # printed checksum folds in a per-frame trace that a restart does not
    # clear, which a final-state checksum could not.
    chase(FOODS_AFTER_RESTART)
    tape.append("q")
    return "".join(tape), game


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="tests-snake-keys.txt")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tape, game = plan()
    print(f"frames={len(tape) - 1} score={game.score} foods={game.foods} "
          f"len={len(game.body)} level={game.level + 1} "
          f"alive={int(game.alive)} checksum=0x{game.checksum():08x}")
    print(f"tape: {tape}")
    if args.dry_run:
        return
    path = pathlib.Path(args.output)
    path.write_text(tape)
    print(f"wrote {path} ({len(tape)} bytes)")


if __name__ == "__main__":
    main()
