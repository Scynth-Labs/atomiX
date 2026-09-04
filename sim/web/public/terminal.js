// The terminal both pages share.
//
// A deliberately small one: the aXos shell and the bare-metal examples emit
// printable ASCII, "\n", "\b \b" to erase, and a handful of CSI sequences.
// Pulling in a full emulator would add far more surface than the machines can
// exercise, so this implements exactly what the consoles actually produce and
// ignores the rest rather than rendering escape sequences as garbage.
//
// It lives in its own file because there are now two pages driving machines,
// and a second copy of a terminal is a second thing that can disagree with the
// first about what the machine printed.
'use strict';

const SCROLLBACK = 2000;

// ---------------------------------------------------------------------------
// Terminal
//
// A deliberately small terminal: the aXos shell emits printable ASCII, "\n",
// "\b \b" to erase, and ESC[2J ESC[H to clear. Pulling in a full emulator
// would add far more surface than the machine can exercise, so this implements
// exactly what the console actually produces and ignores the rest rather than
// rendering escape sequences as garbage.
// ---------------------------------------------------------------------------
class Terminal {
  constructor(element) {
    this.el = element;
    this.reset();
  }

  reset() {
    this.lines = [''];
    this.row = 0;
    this.col = 0;
    this.state = 'text';
    this.params = '';
    this.dirty = true;
  }

  write(text) {
    for (const ch of text) this.writeChar(ch);
    this.dirty = true;
  }

  writeChar(ch) {
    if (this.state === 'escape') {
      this.state = ch === '[' ? 'csi' : 'text';
      this.params = '';
      return;
    }
    if (this.state === 'csi') {
      if (ch >= '@' && ch <= '~') {
        this.controlSequence(ch, this.params);
        this.state = 'text';
      } else {
        this.params += ch;
      }
      return;
    }
    switch (ch) {
      case '\x1b': this.state = 'escape'; return;
      case '\n':
        this.row += 1;
        this.col = 0;
        while (this.lines.length <= this.row) this.lines.push('');
        if (this.lines.length > SCROLLBACK) {
          const drop = this.lines.length - SCROLLBACK;
          this.lines.splice(0, drop);
          this.row -= drop;
        }
        return;
      case '\r': this.col = 0; return;
      case '\b': if (this.col > 0) this.col -= 1; return;
      case '\x07': return;  // bell: nothing worth doing in a page
      default:
        if (ch < ' ' && ch !== '\t') return;
        this.putAtCursor(ch);
    }
  }

  putAtCursor(ch) {
    let line = this.lines[this.row];
    if (line.length < this.col) line = line.padEnd(this.col, ' ');
    this.lines[this.row] = line.slice(0, this.col) + ch + line.slice(this.col + 1);
    this.col += 1;
  }

  controlSequence(final, params) {
    // Private sequences -- ESC[?25l and ESC[?25h are cursor visibility -- are
    // recognised and dropped. The page draws its own cursor, and the
    // alternative to recognising them is printing "?25l" into the game.
    if (params.startsWith('?')) return;
    if (final === 'J' && (params === '2' || params === '')) {
      this.lines = [''];
      this.row = 0;
      this.col = 0;
    } else if (final === 'H' || final === 'f') {
      // Absolute cursor addressing, 1-based, defaulting to the home position
      // when a parameter is omitted. This is the one sequence an interactive
      // game cannot do without: it redraws the cells that changed rather than
      // the whole screen, so a terminal that treats every ESC[r;cH as "home"
      // stacks a whole frame into the top-left corner.
      const numbers = params.split(';').map((value) => parseInt(value, 10));
      this.moveTo((numbers[0] || 1) - 1, (numbers[1] || 1) - 1);
    } else if (final === 'K') {
      this.lines[this.row] = this.lines[this.row].slice(0, this.col);
    }
  }

  moveTo(row, col) {
    this.row = Math.max(0, row);
    this.col = Math.max(0, col);
    while (this.lines.length <= this.row) this.lines.push('');
  }

  // The tail of the current line, which is how the driver recognizes that the
  // shell is sitting at its prompt with nothing to do.
  currentLine() { return this.lines[this.row] || ''; }

  render() {
    if (!this.dirty) return;
    this.dirty = false;
    const escape = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
    const out = [];
    for (let i = 0; i < this.lines.length; ++i) {
      if (i !== this.row) { out.push(escape(this.lines[i])); continue; }
      const line = this.lines[i].padEnd(this.col + 1, ' ');
      out.push(
        escape(line.slice(0, this.col)) +
        '<span class="cursor">' + escape(line[this.col] || ' ') + '</span>' +
        escape(line.slice(this.col + 1)));
    }
    this.el.innerHTML = out.join('\n');
  }
}
