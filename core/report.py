"""Terminal output for the CLI.

Design goals, in priority order:

1. The verdict is unmistakable and comes LAST, so the last thing on screen is
   the answer rather than a warning.
2. A failure can never be mistaken for progress. Failure glyph, the word
   "failed", and the exit code all agree.
3. Default output is scannable. Long explanations are demoted to stderr and
   to -v; nothing is deleted, only folded.
4. "Could not run" is never reported as "failed". A missing dependency is a
   tooling problem, not a shader problem, and conflating them sends people
   looking in the wrong place.

Unicode: emoji are auto-detected. Several render double-width and would break
the indentation, so an ASCII set is used when the stream cannot encode them.
Set VS2_ASCII=1 to force ASCII.
"""
import os
import sys

# --- glyph sets ------------------------------------------------------------

_UNICODE = {'ok': '\u2705', 'warn': '\u26a0\ufe0f', 'fail': '\u274c'}
_ASCII = {'ok': '[ok]', 'warn': '[!]', 'fail': '[X]'}

# Indent for body lines, matched to the glyph actually used. The warning
# emoji renders two cells wide; the ASCII tokens are three characters.
_PAD_UNICODE = 2
_PAD_ASCII = 4

DIM, BOLD, RESET = '\033[2m', '\033[1m', '\033[0m'


def supports_unicode(stream=None) -> bool:
    """Can this stream encode the glyphs? Falls back to ASCII if not."""
    if os.environ.get('VS2_ASCII'):
        return False
    stream = stream or sys.stdout
    enc = (getattr(stream, 'encoding', None) or '').lower()
    if 'utf' in enc:
        return True
    try:
        # probe by encoding, never by writing: the old probe wrote the three
        # glyphs into the caller's stream, polluting any writable stream
        # whose `encoding` is missing. test_cli.UnicodeProbeTests.
        (_UNICODE['ok'] + _UNICODE['warn'] + _UNICODE['fail']).encode(
            enc or 'utf-8')
        return True
    except (LookupError, UnicodeEncodeError):
        return False


class Reporter:
    """Renders the run. Verdict last; detail on stderr and behind -v."""

    def __init__(self, verbose=False, stream=None, errstream=None):
        self.verbose = verbose
        self.out = stream or sys.stdout
        self.err = errstream or sys.stderr
        self.glyphs = _UNICODE if supports_unicode(self.out) else _ASCII
        self._color = self.out.isatty() and not os.environ.get('NO_COLOR')
        self._collecting = False
        self._notes = []
        self._unverified = []

    # -- primitives -------------------------------------------------------
    def _w(self, text=''):
        self.out.write(text + '\n')

    def _paint(self, text, code):
        return (code + text + RESET) if self._color else text

    def _glyph(self, kind):
        return self.glyphs[kind]

    # -- progress ---------------------------------------------------------
    def step(self, text):
        """Progress chatter. Only with -v; the default output is the verdict."""
        if self.verbose:
            self._w('  %s' % self._paint(text, DIM))

    def note(self, text):
        """Collected and shown under the verdict."""
        self._notes.append(text)

    def unverified(self, headline, detail):
        """An assumption the tool could not check. Always shown: it changes
        how much the result can be trusted."""
        self._unverified.append((headline, detail))

    def detail(self, text):
        """Long explanation. stderr, and only with -v."""
        if self.verbose:
            self.err.write(text + '\n')

    # -- verdict ----------------------------------------------------------
    def _line(self, kind, headline, body=None, action=None, footer=None,
              wrote=None):
        pad = ' ' * (_PAD_UNICODE if self.glyphs is _UNICODE
                      else _PAD_ASCII)
        self._w()
        head = '%s %s' % (self._glyph(kind), headline)
        self._w(self._paint(head, BOLD))
        if body:
            for ln in body if isinstance(body, (list, tuple)) else [body]:
                self._w('%s%s' % (pad, ln))
        if action:
            self._w('%s-> %s' % (pad, self._paint(action, DIM)))
        if footer:
            self._w('%s%s' % (pad, self._paint(footer, DIM)))

    def success(self, name, wrote=None, stats=None):
        if isinstance(stats, (list, tuple)):
            stats = ' \u00b7 '.join(str(x) for x in stats)
        self._line('ok', '%s  ->  %s' % (name, wrote) if wrote else name,
                   body=[stats] if stats else None)
        for headline, detail in self._unverified:
            self._line('warn', 'unverified: %s' % headline, body=detail)
        if self._notes:
            self._w()
            import textwrap
            for n in self._notes:
                for i, ln in enumerate(textwrap.wrap(str(n), 66)):
                    self._w('  %s' % self._paint(('note: ' if i == 0 else '      ') + ln, DIM))
        if self.verbose:
            self._w()

    def failure(self, name, headline, body=None, action=None, exit_code=1,
                wrote=False):
        self._line('fail', '%s - %s' % (name, headline), body=body,
                   action=action,
                   footer='exit %d . %s' % (exit_code,
                                            'wrote %s' % wrote if wrote
                                            else 'nothing written'))
        for headline, detail in self._unverified:
            self._line('warn', 'unverified: %s' % headline, body=detail)

    def skipped(self, name, headline, body=None, action=None, exit_code=2):
        """Not a failure: something the run needed did not happen."""
        self._line('warn', '%s - %s' % (name, headline), body=body,
                   action=action,
                   footer='exit %d' % exit_code)
